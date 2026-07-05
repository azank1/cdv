/**
 * Loop Monitor — sidebar panel for PromptLoop
 *
 * The "agent scrum-master" board. Shows:
 *   • DAG runs as a dependency board — one card per node, colored by
 *     verification state, with the plain-language reason a node failed CDV
 *     (from ~/.loopllm/projects/<id>/active_runs/*.json, run_type="dag")
 *   • Active agent-loop sessions (same directory, run_type="agent_loop")
 *   • Recently completed episodes (from episodes_feed.json)
 *
 * All data is read directly from the filesystem — no MCP connection needed.
 * The panel polls every few seconds via a setInterval driven from extension.ts.
 */

import * as vscode from "vscode";
import * as cp from "child_process";
import * as fs from "fs";
import * as path from "path";

/** Build a PATH that includes common loopllm install locations (mirrors extension.ts). */
function buildEnv(): NodeJS.ProcessEnv {
  const home = process.env.HOME ?? "";
  const extras = [
    `${home}/.local/bin`,
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
  ].join(":");
  return { ...process.env, PATH: `${extras}:${process.env.PATH ?? ""}` };
}

// ─── Types ────────────────────────────────────────────────────────────────────

interface AgentLoopState {
  session_id: string;
  goal: string;
  task_type: string;
  model_id: string;
  quality_threshold: number;
  suggested_budget: number;
  scores: number[];
  last_decision?: string;
  last_reason?: string;
  converged?: boolean | null;
  closed?: boolean;
  started_at?: number;
  last_step_at?: number;
  step_outputs?: string[];
}

type DagNodeState = "pending" | "ready" | "running" | "verified" | "failed";

interface DagNode {
  id: string;
  role: string;
  description: string;
  dependencies: string[];
  state: DagNodeState;
  verified_score: number;
  verified_output: string;
  deficiencies: string[];
  evaluator_type: string;
  quality_criteria: string[];
}

interface DagRunState {
  run_id: string;
  goal: string;
  task_type: string;
  model_id: string;
  closed: boolean;
  merged_output: string;
  nodes: Record<string, DagNode>;
  ready: string[];
}

interface ActiveRun {
  run_id: string;
  run_type: "agent_loop" | "dag" | string;
  state: AgentLoopState & Partial<DagRunState>;
}

interface EpisodeFeedEntry {
  episode_type: string;
  goal: string;
  task_type: string;
  model_id?: string;
  score_final?: number | null;
  steps_used?: number | null;
  stop_reason?: string | null;
  recorded_at: string;
}

interface ConsultationRecord {
  last_consulted_at: number;
  last_tool: string;
}

// ─── Provider ────────────────────────────────────────────────────────────────

export class LoopMonitorProvider implements vscode.WebviewViewProvider {
  public static readonly viewId = "loopllm.loopMonitor";

  private _view?: vscode.WebviewView;
  private readonly _loopllmDir: string;
  private readonly _dbPath: string;
  private readonly _activationTime = Date.now();
  private _hasEditActivity = false;

  constructor(dbPath: string) {
    this._dbPath = dbPath;
    this._loopllmDir = path.dirname(dbPath);
  }

  /**
   * Called from extension.ts on any file save/edit — the "this session has
   * real work happening" half of the not-consulted signal. Without this,
   * a freshly opened, untouched workspace would always show the banner.
   */
  markEditActivity(): void {
    this._hasEditActivity = true;
  }

  resolveWebviewView(
    view: vscode.WebviewView,
    _ctx: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken,
  ): void {
    this._view = view;
    view.webview.options = { enableScripts: true };
    view.webview.html = this._html();
    view.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === "exportAudit") {
        this._exportAuditReport();
      }
    });
    // Initial paint
    this.refresh();
  }

  /**
   * Run `loopllm audit` for the board's own project db and open the
   * human-readable verification trail in a new editor tab — the "team
   * artifact" a reviewer can read or save (Cmd+S) independent of the IDE.
   */
  private _exportAuditReport(): void {
    cp.execFile(
      "loopllm",
      ["--db", this._dbPath, "audit"],
      { timeout: 10000, env: buildEnv(), maxBuffer: 5 * 1024 * 1024 },
      async (err, stdout, stderr) => {
        const body = err
          ? `# PromptLoop Verification Audit\n\nFailed to run \`loopllm audit\`: ${err.message}\n\n${stderr}`
          : `# PromptLoop Verification Audit\n\n\`\`\`\n${stdout.trim()}\n\`\`\`\n`;
        const doc = await vscode.workspace.openTextDocument({
          content: body,
          language: "markdown",
        });
        await vscode.window.showTextDocument(doc, { preview: false });
      }
    );
  }

  /** Called by the poll timer in extension.ts */
  refresh(): void {
    if (!this._view?.webview) { return; }
    const runs = this._readActiveRuns();
    const episodes = this._readEpisodesFeed();
    const notConsulted = this._computeNotConsulted();
    this._view.webview.postMessage({ type: "update", runs, episodes, notConsulted });
  }

  /**
   * The "PromptLoop not consulted this session" signal: true only when the
   * user has actually been editing (markEditActivity) AND no entry-point
   * tool (intercept / loop_start / loop_step / dag_compile / dag_submit) has
   * fired since this panel activated. A fresh, untouched workspace or a
   * session that genuinely called loopllm never shows the banner.
   */
  private _computeNotConsulted(): boolean {
    if (!this._hasEditActivity) { return false; }
    const record = this._readConsultation();
    if (record === null) { return true; }
    return record.last_consulted_at * 1000 < this._activationTime;
  }

  // ─── Data readers ──────────────────────────────────────────────────────────

  private _readConsultation(): ConsultationRecord | null {
    const consultationPath = path.join(this._loopllmDir, "consultation.json");
    try {
      if (!fs.existsSync(consultationPath)) { return null; }
      const data = JSON.parse(fs.readFileSync(consultationPath, "utf-8"));
      if (typeof data?.last_consulted_at === "number") {
        return data as ConsultationRecord;
      }
      return null;
    } catch { return null; }
  }

  private _readActiveRuns(): ActiveRun[] {
    const dir = path.join(this._loopllmDir, "active_runs");
    try {
      if (!fs.existsSync(dir)) { return []; }
      return fs.readdirSync(dir)
        .filter((f) => f.endsWith(".json"))
        .map((f) => {
          try {
            return JSON.parse(fs.readFileSync(path.join(dir, f), "utf-8")) as ActiveRun;
          } catch { return null; }
        })
        .filter((r): r is ActiveRun => r !== null);
    } catch { return []; }
  }

  private _readEpisodesFeed(): EpisodeFeedEntry[] {
    const feedPath = path.join(this._loopllmDir, "episodes_feed.json");
    try {
      if (!fs.existsSync(feedPath)) { return []; }
      const data = JSON.parse(fs.readFileSync(feedPath, "utf-8"));
      return Array.isArray(data) ? (data as EpisodeFeedEntry[]).slice().reverse().slice(0, 10) : [];
    } catch { return []; }
  }

  // ─── HTML ──────────────────────────────────────────────────────────────────

  private _html(): string {
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Loop Monitor</title>
<style>
  :root {
    --bg:      var(--vscode-editor-background);
    --fg:      var(--vscode-editor-foreground);
    --fg-dim:  var(--vscode-descriptionForeground);
    --border:  var(--vscode-panel-border);
    --accent:  var(--vscode-focusBorder);
    --input-bg: var(--vscode-input-background);
    --badge-bg: var(--vscode-badge-background);
    --badge-fg: var(--vscode-badge-foreground);
    --success: #4caf50;
    --warn:    #ff9800;
    --danger:  #f44336;
    --info:    #2196f3;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--vscode-font-family, system-ui);
    font-size: var(--vscode-font-size, 13px);
    color: var(--fg);
    background: var(--bg);
    padding: 8px;
    user-select: none;
  }

  h3 {
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fg-dim);
    margin: 12px 0 6px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border);
  }
  h3:first-of-type { margin-top: 0; }

  .empty {
    font-size: 12px;
    color: var(--fg-dim);
    padding: 6px 0;
    font-style: italic;
  }

  /* Active loop cards */
  .loop-card {
    background: var(--input-bg);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 4px;
    padding: 8px 10px;
    margin-bottom: 6px;
  }
  .loop-card.stopped { border-left-color: var(--success); opacity: 0.75; }

  .loop-goal {
    font-weight: 600;
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 4px;
  }
  .loop-meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
    margin-bottom: 6px;
  }
  .badge {
    font-size: 10px;
    font-weight: 600;
    padding: 1px 6px;
    border-radius: 10px;
    background: var(--badge-bg);
    color: var(--badge-fg);
    white-space: nowrap;
  }
  .badge.green  { background: var(--success); color: #fff; }
  .badge.orange { background: var(--warn);    color: #000; }
  .badge.red    { background: var(--danger);  color: #fff; }
  .badge.blue   { background: var(--info);    color: #fff; }

  /* Score bar */
  .score-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 4px;
  }
  .score-label { font-size: 11px; color: var(--fg-dim); width: 58px; flex-shrink: 0; }
  .score-bar-wrap {
    flex: 1;
    height: 6px;
    background: var(--border);
    border-radius: 3px;
    overflow: hidden;
  }
  .score-bar {
    height: 100%;
    border-radius: 3px;
    transition: width 0.4s ease;
  }
  .score-num { font-size: 11px; width: 32px; text-align: right; flex-shrink: 0; }

  .loop-reason {
    font-size: 11px;
    color: var(--fg-dim);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-top: 4px;
  }

  /* Episode rows */
  .ep-row {
    display: grid;
    grid-template-columns: 1fr auto auto;
    gap: 4px 8px;
    align-items: start;
    padding: 5px 0;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .ep-row:last-child { border-bottom: none; }
  .ep-goal {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 12px;
  }
  .ep-score { font-weight: 600; font-size: 11px; text-align: right; }
  .ep-meta  { font-size: 10px; color: var(--fg-dim); text-align: right; white-space: nowrap; }
  .ep-stop  { grid-column: 1 / -1; font-size: 10px; color: var(--fg-dim); }

  .pulse {
    display: inline-block;
    width: 7px; height: 7px;
    border-radius: 50%;
    background: var(--success);
    animation: pulse 1.5s ease-in-out infinite;
    margin-right: 5px;
    vertical-align: middle;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.3; }
  }

  /* DAG board */
  .dag-run {
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 8px 10px;
    margin-bottom: 10px;
    background: var(--input-bg);
  }
  .dag-run-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-bottom: 8px;
  }
  .dag-run-goal {
    font-weight: 600;
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }
  .dag-board {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 6px;
  }
  .dag-col-title {
    font-size: 9px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--fg-dim);
    margin-bottom: 4px;
  }
  .dag-node {
    border-radius: 4px;
    border: 1px solid var(--border);
    border-left: 3px solid var(--fg-dim);
    background: var(--bg);
    padding: 6px 7px;
    margin-bottom: 5px;
    font-size: 11px;
  }
  .dag-node.pending  { border-left-color: var(--fg-dim); opacity: 0.6; }
  .dag-node.ready    { border-left-color: var(--info); }
  .dag-node.running  { border-left-color: var(--warn); }
  .dag-node.verified { border-left-color: var(--success); }
  .dag-node.failed   { border-left-color: var(--danger); }
  .dag-node-id {
    font-weight: 600;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 4px;
    margin-bottom: 2px;
  }
  .dag-node-desc {
    color: var(--fg-dim);
    font-size: 10px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    margin-bottom: 3px;
  }
  .dag-node-score {
    font-size: 10px;
    font-weight: 600;
  }
  .dag-node-why {
    margin-top: 4px;
    padding-top: 4px;
    border-top: 1px dashed var(--border);
    font-size: 10px;
    color: var(--danger);
  }
  .dag-node-why-item { margin-bottom: 1px; }
  .dag-empty-col {
    font-size: 10px;
    color: var(--fg-dim);
    font-style: italic;
    opacity: 0.7;
  }

  .board-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
  }
  .export-btn {
    font-size: 10px;
    padding: 2px 8px;
    border-radius: 3px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--fg-dim);
    cursor: pointer;
  }
  .export-btn:hover { color: var(--fg); border-color: var(--accent); }

  /* Not-consulted banner — deliberately not dismissable; it clears itself
     the moment an entry-point tool is actually called. */
  .not-consulted-banner {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--warn);
    color: #000;
    font-size: 11px;
    font-weight: 600;
    padding: 6px 8px;
    border-radius: 4px;
    margin-bottom: 10px;
  }
  .not-consulted-banner .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    background: #000;
    flex-shrink: 0;
  }
</style>
</head>
<body>

<div id="not-consulted"></div>

<div class="board-header">
  <h3 style="margin:0;border-bottom:none;padding-bottom:0;">DAG Runs <span style="font-weight:400;text-transform:none;letter-spacing:normal;">(agent scrum-master)</span></h3>
  <button class="export-btn" onclick="exportAudit()">Export audit</button>
</div>
<div id="dag-runs"><p class="empty">No DAG runs — call loopllm_dag_compile to decompose a complex goal</p></div>

<h3>Active Loops</h3>
<div id="active-loops"><p class="empty">No active loops</p></div>

<h3>Recent Episodes</h3>
<div id="episodes"><p class="empty">No episodes recorded yet</p></div>

<script>
const vscode = acquireVsCodeApi();

function exportAudit() {
  vscode.postMessage({ type: 'exportAudit' });
}

function scoreColor(s) {
  if (s == null) return '#888';
  if (s >= 0.8) return '#4caf50';
  if (s >= 0.6) return '#ff9800';
  return '#f44336';
}

function scorePct(s) {
  return s == null ? 0 : Math.round(s * 100);
}

function fmtScore(s) {
  return s == null ? '—' : s.toFixed(2);
}

function decisionBadge(d) {
  if (!d) return '';
  const cls = d === 'stop' ? 'green' : 'blue';
  return '<span class="badge ' + cls + '">' + d.toUpperCase() + '</span>';
}

function timeSince(isoStr) {
  if (!isoStr) return '';
  const d = new Date(isoStr.endsWith('Z') ? isoStr : isoStr + 'Z');
  const secs = Math.floor((Date.now() - d.getTime()) / 1000);
  if (secs < 60)  return secs + 's ago';
  if (secs < 3600) return Math.floor(secs / 60) + 'm ago';
  return Math.floor(secs / 3600) + 'h ago';
}

const DAG_COLUMNS = [
  { key: 'pending',  label: 'Pending' },
  { key: 'ready',    label: 'Ready' },
  { key: 'running',  label: 'Running' },
  { key: 'verified', label: 'Verified' },
  { key: 'failed',   label: 'Failed' },
];

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function renderDagNode(node) {
  const score = node.verified_score;
  const showScore = node.state === 'verified' || node.state === 'failed';
  const deficiencies = node.deficiencies || [];
  const why = (node.state === 'failed' && deficiencies.length)
    ? '<div class="dag-node-why">' +
        deficiencies.slice(0, 3).map(d => '<div class="dag-node-why-item">· ' + escapeHtml(d) + '</div>').join('') +
      '</div>'
    : '';
  return \`<div class="dag-node \${node.state}">
    <div class="dag-node-id">
      <span title="\${escapeHtml(node.id)} (\${escapeHtml(node.role)})">\${escapeHtml(node.id)}</span>
      \${showScore ? '<span class="dag-node-score" style="color:' + scoreColor(score) + '">' + fmtScore(score) + '</span>' : ''}
    </div>
    <div class="dag-node-desc" title="\${escapeHtml(node.description)}">\${escapeHtml(node.description || node.role)}</div>
    \${why}
  </div>\`;
}

function renderDagRuns(runs) {
  const el = document.getElementById('dag-runs');
  const dagRuns = (runs || []).filter(r => r.run_type === 'dag' && r.state && r.state.nodes);
  if (dagRuns.length === 0) {
    el.innerHTML = '<p class="empty">No DAG runs — call loopllm_dag_compile to decompose a complex goal</p>';
    return;
  }
  el.innerHTML = dagRuns.map(run => {
    const s = run.state;
    const nodes = Object.values(s.nodes || {});
    const verifiedCount = nodes.filter(n => n.state === 'verified').length;
    const failedCount = nodes.filter(n => n.state === 'failed').length;
    const goal = (s.goal || run.run_id).slice(0, 80);
    const columns = DAG_COLUMNS.map(col => {
      const inCol = nodes.filter(n => n.state === col.key);
      const body = inCol.length
        ? inCol.map(renderDagNode).join('')
        : '<p class="dag-empty-col">—</p>';
      return \`<div>
        <div class="dag-col-title">\${col.label} (\${inCol.length})</div>
        \${body}
      </div>\`;
    }).join('');
    return \`<div class="dag-run">
      <div class="dag-run-header">
        <span class="dag-run-goal" title="\${escapeHtml(s.goal)}">\${escapeHtml(goal)}</span>
        <span class="badge \${s.closed ? 'green' : 'blue'}">\${s.closed ? 'MERGED' : verifiedCount + '/' + nodes.length + ' verified'}</span>
        \${failedCount ? '<span class="badge red">' + failedCount + ' failed</span>' : ''}
      </div>
      <div class="dag-board">\${columns}</div>
    </div>\`;
  }).join('');
}

function renderActiveLoops(runs) {
  const el = document.getElementById('active-loops');
  const loopRuns = (runs || []).filter(r => r.run_type !== 'dag');
  if (!loopRuns || loopRuns.length === 0) {
    el.innerHTML = '<p class="empty">No active loops</p>';
    return;
  }
  el.innerHTML = loopRuns.map(run => {
    const s = run.state || {};
    const scores = s.scores || [];
    const lastScore = scores.length ? scores[scores.length - 1] : null;
    const step = scores.length;
    const budget = s.suggested_budget || '?';
    const stopped = s.closed || s.last_decision === 'stop';
    const cardCls = stopped ? 'loop-card stopped' : 'loop-card';
    const goal = (s.goal || run.run_id).slice(0, 80);
    const taskType = s.task_type || 'general';
    const pct = scorePct(lastScore);
    const col = scoreColor(lastScore);
    const reason = (s.last_reason || '').slice(0, 90);

    return \`<div class="\${cardCls}">
      <div class="loop-goal">
        \${stopped ? '' : '<span class="pulse"></span>'}
        \${goal}
      </div>
      <div class="loop-meta">
        <span class="badge">\${taskType}</span>
        <span class="badge \${stopped ? 'green' : 'orange'}">Step \${step}/\${budget}</span>
        \${decisionBadge(s.last_decision)}
      </div>
      <div class="score-row">
        <span class="score-label">Last score</span>
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:\${pct}%;background:\${col}"></div>
        </div>
        <span class="score-num">\${fmtScore(lastScore)}</span>
      </div>
      <div class="score-row">
        <span class="score-label">Threshold</span>
        <div class="score-bar-wrap">
          <div class="score-bar" style="width:\${scorePct(s.quality_threshold)}%;background:#888"></div>
        </div>
        <span class="score-num">\${fmtScore(s.quality_threshold)}</span>
      </div>
      \${reason ? '<div class="loop-reason">' + reason + '</div>' : ''}
    </div>\`;
  }).join('');
}

function renderEpisodes(eps) {
  const el = document.getElementById('episodes');
  if (!eps || eps.length === 0) {
    el.innerHTML = '<p class="empty">No episodes recorded yet</p>';
    return;
  }
  el.innerHTML = eps.map(ep => {
    const score = ep.score_final;
    const col = scoreColor(score);
    const steps = ep.steps_used != null ? ep.steps_used + ' steps' : '';
    const stopLabel = ep.stop_reason ? ep.stop_reason.replace(/_/g, ' ') : '';
    const ago = timeSince(ep.recorded_at);
    const goal = (ep.goal || '(unknown)').slice(0, 70);
    return \`<div class="ep-row">
      <span class="ep-goal" title="\${ep.goal}">\${goal}</span>
      <span class="ep-score" style="color:\${col}">\${fmtScore(score)}</span>
      <span class="ep-meta">\${ep.task_type || ''}</span>
      <span class="ep-stop">\${steps}\${steps && stopLabel ? ' · ' : ''}\${stopLabel}\${ago ? ' · ' + ago : ''}</span>
    </div>\`;
  }).join('');
}

function renderNotConsulted(notConsulted) {
  const el = document.getElementById('not-consulted');
  el.innerHTML = notConsulted
    ? '<div class="not-consulted-banner"><span class="dot"></span>' +
        'PromptLoop has not been consulted this session — the agent may be ' +
        'editing without verification. Ask it to call loopllm_intercept or ' +
        'loopllm_dag_compile.</div>'
    : '';
}

window.addEventListener('message', ev => {
  const msg = ev.data;
  if (msg.type === 'update') {
    renderNotConsulted(msg.notConsulted);
    renderDagRuns(msg.runs);
    renderActiveLoops(msg.runs);
    renderEpisodes(msg.episodes);
  }
});
</script>
</body>
</html>`;
  }
}
