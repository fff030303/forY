"use client";

import { FormEvent, useEffect, useState } from "react";


type Project = {
  id: string;
  display_name: string;
  relationship_type: string;
  status: string;
  target_speaker?: string;
  user_speaker?: string;
  active_version_id?: string;
};

type Participant = {name: string; message_count: number};
type ImportReport = {
  inserted_count: number;
  duplicate_count: number;
  invalid_count: number;
  participants: Participant[];
  privacy_findings: {type: string; count: number}[];
  can_build: boolean;
};
type WechatStatus = {
  installed: boolean;
  ready: boolean;
  version?: string;
  detail?: string;
  setup_command?: string;
};
type WechatSession = {
  name: string;
  chat_type: string;
  last_message?: string;
  last_timestamp?: string | number;
};
type WechatPreview = {
  message_count: number;
  participants: string[];
  start_time?: string | number;
  end_time?: string | number;
  sample: {speaker: string; text: string; timestamp?: string | number}[];
};
type WechatImportJob = {
  id: string;
  source_chat: string;
  self_speaker: string;
  status: "queued" | "importing" | "segmenting" | "analyzing" | "merging" | "completed" | "failed";
  next_offset: number;
  imported_count: number;
  duplicate_count: number;
  chunk_count: number;
  analyzed_chunk_count: number;
  import_complete: number;
  error?: string;
  created_at: string;
  updated_at: string;
  participants: Participant[];
  can_build: boolean;
};
type Trait = {
  name: string;
  value: string;
  confidence: number;
  evidence: string;
  source_message_ids?: string[];
  human_corrected?: boolean;
};
type Memory = {
  id: string;
  content: string;
  importance: number;
  event_date?: string;
  source_message_ids: string[];
};
type Example = {id: string; context_text: string; reply_text: string};
type Version = {
  id: string;
  version_number: number;
  summary: string;
  created_at: string;
  traits?: Trait[];
  relationship?: {
    affect_profile?: AffectProfile;
    emotional_episodes?: AnalysisPattern[];
    emotional_patterns?: AnalysisPattern[];
    relationship_patterns?: AnalysisPattern[];
    conflict_patterns?: AnalysisPattern[];
    needs_and_boundaries?: AnalysisPattern[];
    temporal_changes?: AnalysisPattern[];
  };
};
type AffectProfile = {
  baseline?: string;
  reactivity?: string;
  expression?: string;
  regulation?: string;
  recovery?: string;
  relationship_orientation?: string;
  humor_style?: string;
  confidence?: number;
  evidence_message_ids?: string[];
};
type AnalysisPattern = {
  title?: string;
  name?: string;
  emotion?: string;
  description?: string;
  type?: string;
  triggers?: string;
  trigger?: string;
  expression?: string;
  regulation?: string;
  reaction?: string;
  repair?: string;
  toward_user?: string;
  earlier?: string;
  recent?: string;
  facts?: string[];
  initial_emotion?: string;
  peak_emotion?: string;
  coping?: string;
  social_function?: string;
  relationship_signal?: string;
  confidence?: number;
  evidence_message_ids?: string[];
};
type Persona = {
  project: Project;
  version: Version & {traits: Trait[]};
  memories: Memory[];
  memory_candidates: Memory[];
  examples: Example[];
};
type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  tone?: string;
  expression?: string;
  feedback?: "like" | "dislike";
};
type RuntimeLocation = {latitude: number; longitude: number};
type RuntimeContext = {
  period: string;
  location_authorized: boolean;
  life_state?: LifeState;
  weather?: {
    condition: string;
    temperature_c: number;
    feels_like_c?: number;
  };
};
type LifeState = {
  activity: string;
  activity_code: string;
  activity_started_at: string;
  location: string;
  mood: string;
  condition: string;
  energy: number | null;
  hunger: number | null;
  sleepiness: number | null;
  health: number | null;
  stress: number | null;
  confidence: number;
  basis: string;
  evidence_message_ids: string[];
  recent_evidence_message_ids: string[];
  last_simulated_at: string;
};
type RoutineInsight = {
  kind: string;
  label: string;
  typical_time: string;
  evidence_count: number;
  independent_days: number;
  confidence: number;
  usable: boolean;
  predictable: boolean;
  scope: string;
  evidence_message_ids: string[];
  basis: string;
};
type LifeEvent = {
  id: string;
  event_type: string;
  title: string;
  description: string;
  location: string;
  started_at: string;
  source: "evidence_inference" | "user_guided_ai";
  confidence: number;
  evidence_message_ids: string[];
  basis: string;
};
type LifeSnapshot = {
  state: LifeState;
  events: LifeEvent[];
  routine_profile: RoutineInsight[];
  guidance: string;
  daily_plan?: {
    summary: string;
    events: unknown[];
  };
  date: string;
  is_simulated: boolean;
  notice: string;
};
type EvidenceMessage = {
  id: string;
  speaker: string;
  sent_at?: string;
  text: string;
};


const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
const SAMPLE_CHAT = `[2025-02-01 09:00] 我: 明天要面试了，有点慌
[2025-02-01 09:01] 小林: 别急，你准备得挺充分的
[2025-02-01 09:02] 我: 你还记得上次我们一起练习吗
[2025-02-01 09:03] 小林: 记得啊，你后来不是发挥得很好吗？
[2025-02-02 12:00] 我: 中午吃什么
[2025-02-02 12:01] 小林: 还是你喜欢的那家面馆？`;


class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}


async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...options,
    headers: {"Content-Type": "application/json", ...options?.headers},
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(payload?.detail ?? "请求失败，请稍后重试", response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json();
}


function remainingTime(job: WechatImportJob): string | null {
  if (job.status !== "analyzing" || job.analyzed_chunk_count < 1) return null;
  const elapsedSeconds = (
    Date.parse(job.updated_at) - Date.parse(job.created_at)
  ) / 1000;
  if (!Number.isFinite(elapsedSeconds) || elapsedSeconds <= 0) return null;
  const seconds = Math.ceil(
    elapsedSeconds / job.analyzed_chunk_count
    * (job.chunk_count - job.analyzed_chunk_count)
  );
  if (seconds < 60) return "不到 1 分钟";
  const minutes = Math.ceil(seconds / 60);
  return minutes < 60 ? `约 ${minutes} 分钟` : `约 ${Math.ceil(minutes / 60)} 小时`;
}


export default function Home() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [persona, setPersona] = useState<Persona | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [view, setView] = useState<"setup" | "chat" | "life" | "persona" | "memory" | "versions">("setup");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<Project[]>("/projects")
      .then(setProjects)
      .catch(() => setError("无法连接 API，请先启动后端服务。"));
  }, []);

  async function loadProject(selected: Project) {
    setProject(selected);
    setReport(null);
    if (selected.active_version_id) {
      const [personaData, versionData] = await Promise.all([
        api<Persona>(`/projects/${selected.id}/persona`),
        api<Version[]>(`/projects/${selected.id}/versions`),
      ]);
      setPersona(personaData);
      setProject(personaData.project);
      setVersions(versionData);
      setView("chat");
    } else {
      setPersona(null);
      setView("setup");
    }
  }

  async function refreshPersona(projectId: string) {
    const [personaData, versionData] = await Promise.all([
      api<Persona>(`/projects/${projectId}/persona`),
      api<Version[]>(`/projects/${projectId}/versions`),
    ]);
    setPersona(personaData);
    setProject(personaData.project);
    setVersions(versionData);
  }

  return (
    <main className="app-shell">
      <aside className="rail">
        <div className="brand">
          <span className="brand-mark">见字</span>
          <span className="brand-note">Persona archive</span>
        </div>
        <div className="project-switcher">
          <label htmlFor="project-select">人格档案</label>
          <select
            id="project-select"
            onChange={(event) => {
              const selected = projects.find((item) => item.id === event.target.value);
              if (selected) loadProject(selected).catch((reason) => setError(reason.message));
              else {
                setProject(null);
                setPersona(null);
                setView("setup");
              }
            }}
            value={project?.id ?? ""}
          >
            <option value="">新建人格</option>
            {projects.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}
          </select>
        </div>

        <section className="persona-card compact">
          <div className="avatar" aria-hidden="true">{project?.display_name.slice(0, 1) ?? "新"}</div>
          <p className="eyebrow">当前人格</p>
          <h1>{project?.display_name ?? "未命名"}</h1>
          <p className="relationship">
            {project ? `${project.relationship_type} · ${persona ? `V${persona.version.version_number}` : "构建中"}` : "等待导入聊天记录"}
          </p>
          {persona && <div className="trait-list">
            {persona.version.traits.slice(0, 3).map((trait, index) => (
              <span key={`${trait.name}-${trait.value}-${index}`}>{trait.value}</span>
            ))}
          </div>}
        </section>

        <nav className="side-nav" aria-label="人格管理">
          {(["chat", "life", "persona", "memory", "versions"] as const).map((item) => (
            <button
              className={view === item ? "active" : ""}
              disabled={!persona}
              key={item}
              onClick={() => setView(item)}
              type="button"
            >
              {{chat: "对话", life: "虚拟日常", persona: "人格画像", memory: "共同记忆", versions: "版本记录"}[item]}
            </button>
          ))}
          <button className={view === "setup" ? "active" : ""} onClick={() => setView("setup")} type="button">
            数据与设置
          </button>
        </nav>
        <p className="disclosure">这是基于聊天记录生成的 AI 模拟人格，不是现实人物本人。</p>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <p className="eyebrow">Private companion workspace</p>
            <h2>{view === "setup" ? "把聊天记录，变成可理解的人格" : project?.display_name}</h2>
          </div>
          <span className="status"><i /> 本地 V1</span>
        </header>
        {(error || notice) && (
          <div className={error ? "banner error-banner" : "banner"}>
            <span>{error || notice}</span>
            <button onClick={() => { setError(""); setNotice(""); }} type="button">关闭</button>
          </div>
        )}

        {view === "setup" && (
          <SetupView
            busy={busy}
            onBuilt={async (data) => {
              setPersona(data);
              setProject(data.project);
              const nextProjects = await api<Project[]>("/projects");
              setProjects(nextProjects);
              setVersions(await api<Version[]>(`/projects/${data.project.id}/versions`));
              setView("persona");
              setNotice("人格初稿已经生成。每条结论都保留了统计证据。");
            }}
            onBusy={setBusy}
            onDeleted={async () => {
              if (!project) return;
              await api(`/projects/${project.id}`, {method: "DELETE"});
              setProjects((current) => current.filter((item) => item.id !== project.id));
              setProject(null);
              setPersona(null);
              setReport(null);
              setNotice("人格项目和关联聊天、记忆、版本已经删除。");
            }}
            onError={setError}
            onProject={(value) => {
              setProject(value);
              setProjects((current) => [value, ...current.filter((item) => item.id !== value.id)]);
            }}
            onReport={setReport}
            project={project}
            report={report}
          />
        )}
        {view === "chat" && persona && (
          <ChatView
            persona={persona}
            onCandidate={() => setNotice("反馈已进入候选区。到“版本记录”手动发布后才会生效。")}
            onError={setError}
            onMemoryCandidate={() => {
              refreshPersona(persona.project.id).catch((reason) => setError(reason.message));
              setNotice("发现一条可能值得长期记住的新事实，请到“共同记忆”确认。");
            }}
          />
        )}
        {view === "life" && persona && (
          <LifeView onError={setError} persona={persona} />
        )}
        {view === "persona" && persona && (
          <PersonaView
            onChanged={() => refreshPersona(persona.project.id).catch((reason) => setError(reason.message))}
            onError={setError}
            persona={persona}
          />
        )}
        {view === "memory" && persona && (
          <MemoryView
            memories={persona.memories}
            candidates={persona.memory_candidates}
            onChanged={() => refreshPersona(persona.project.id).catch((reason) => setError(reason.message))}
            onError={setError}
            projectId={persona.project.id}
          />
        )}
        {view === "versions" && persona && (
          <VersionsView
            activeId={persona.project.active_version_id}
            onChanged={() => refreshPersona(persona.project.id).catch((reason) => setError(reason.message))}
            onError={setError}
            projectId={persona.project.id}
            versions={versions}
          />
        )}
      </section>
    </main>
  );
}


function SetupView({
  project, report, busy, onProject, onReport, onBuilt, onBusy, onDeleted, onError,
}: {
  project: Project | null;
  report: ImportReport | null;
  busy: string;
  onProject: (project: Project) => void;
  onReport: (report: ImportReport) => void;
  onBuilt: (persona: Persona) => void;
  onBusy: (value: string) => void;
  onDeleted: () => Promise<void>;
  onError: (value: string) => void;
}) {
  const [name, setName] = useState("小林");
  const [relationship, setRelationship] = useState("老朋友");
  const [consent, setConsent] = useState(false);
  const [format, setFormat] = useState<"wechat_text" | "jsonl">("wechat_text");
  const [importMode, setImportMode] = useState<"wechat" | "paste">("wechat");
  const [content, setContent] = useState(SAMPLE_CHAT);
  const [target, setTarget] = useState("");
  const [self, setSelf] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [wechatStatus, setWechatStatus] = useState<WechatStatus | null>(null);
  const [sessions, setSessions] = useState<WechatSession[]>([]);
  const [selectedChat, setSelectedChat] = useState("");
  const [wechatSelf, setWechatSelf] = useState("我");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [wechatPreview, setWechatPreview] = useState<WechatPreview | null>(null);
  const [importJob, setImportJob] = useState<WechatImportJob | null>(null);
  const [confirmReanalysis, setConfirmReanalysis] = useState(false);

  async function run(label: string, action: () => Promise<void>) {
    onBusy(label);
    onError("");
    try { await action(); } catch (reason) {
      onError(reason instanceof Error ? reason.message : "操作失败");
    } finally { onBusy(""); }
  }

  const wechatRequest = {
    chat: selectedChat,
    self_speaker: wechatSelf,
    since: since || null,
    until: until || null,
    limit: 50,
  };

  function applyCompletedImport(value: WechatImportJob) {
    onReport({
      inserted_count: value.imported_count,
      duplicate_count: value.duplicate_count,
      invalid_count: 0,
      participants: value.participants,
      privacy_findings: [],
      can_build: value.can_build,
    });
    setTarget(value.source_chat);
    setSelf(value.self_speaker);
  }

  useEffect(() => {
    if (!project) {
      setImportJob(null);
      return;
    }
    api<WechatImportJob[]>(`/projects/${project.id}/wechat/import-jobs`)
      .then((jobs) => {
        const latest = jobs[0] ?? null;
        setImportJob(latest);
        if (latest?.status === "completed") applyCompletedImport(latest);
      })
      .catch(() => undefined);
  }, [project?.id]);

  useEffect(() => {
    if (!project || !importJob || ["completed", "failed"].includes(importJob.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      api<WechatImportJob>(`/projects/${project.id}/wechat/import-jobs/${importJob.id}`)
        .then((value) => {
          setImportJob(value);
          if (value.status === "completed") {
            applyCompletedImport(value);
          }
        })
        .catch((reason) => {
          if (reason instanceof ApiError && reason.status === 404) {
            setImportJob(null);
            return;
          }
          onError(reason instanceof Error ? reason.message : "读取导入进度失败");
        });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [importJob?.id, importJob?.status, project?.id, selectedChat, wechatSelf]);

  async function detectWechat() {
    const status = await api<WechatStatus>("/wechat/status");
    setWechatStatus(status);
    setSessions([]);
    setWechatPreview(null);
    if (status.ready) {
      const result = await api<{sessions: WechatSession[]}>("/wechat/sessions?limit=100");
      setSessions(result.sessions);
      setSelectedChat(result.sessions[0]?.name ?? "");
    }
  }

  return (
    <div className="setup-grid">
      <section className={`step-card ${project ? "complete" : "current"}`}>
        <div className="step-number">01</div>
        <div>
          <p className="eyebrow">建立档案</p>
          <h3>这个人格是谁？</h3>
          <p className="muted-copy">名称只是显示用；数据会按项目独立保存。</p>
          <div className="field-grid">
            <label>人格名称<input disabled={!!project} onChange={(e) => setName(e.target.value)} value={name} /></label>
            <label>你们的关系<input disabled={!!project} onChange={(e) => setRelationship(e.target.value)} value={relationship} /></label>
          </div>
          {!project && <label className="check-row">
            <input checked={consent} onChange={(e) => setConsent(e.target.checked)} type="checkbox" />
            我确认有权使用这份聊天记录，并理解这是 AI 模拟。
          </label>}
          {!project && <button
            className="primary"
            disabled={!name.trim() || !relationship.trim() || !consent || !!busy}
            onClick={() => run("正在创建…", async () => {
              onProject(await api<Project>("/projects", {
                method: "POST",
                body: JSON.stringify({display_name: name, relationship_type: relationship, consent_confirmed: consent}),
              }));
            })}
            type="button"
          >{busy || "创建档案"}</button>}
          {project && <span className="done-mark">已创建 · {project.display_name}</span>}
        </div>
      </section>

      <section className={`step-card ${project && !report ? "current" : report ? "complete" : ""}`}>
        <div className="step-number">02</div>
        <div>
          <p className="eyebrow">导入与清洗</p>
          <h3>选择聊天记录来源</h3>
          <div className="source-tabs">
            <button className={importMode === "wechat" ? "selected" : ""} onClick={() => setImportMode("wechat")} type="button">直接连接微信</button>
            <button className={importMode === "paste" ? "selected" : ""} onClick={() => setImportMode("paste")} type="button">粘贴或 JSONL</button>
          </div>

          {importMode === "wechat" && <div className="wechat-connect">
            <div className="local-only"><i />原始记录先保存在本机；分段后的必要内容会发送给 DeepSeek 分析。</div>
            {!wechatStatus && <button
              className="primary"
              disabled={!project || !!busy}
              onClick={() => run("正在检测…", detectWechat)}
              type="button"
            >检测本机微信</button>}

            {wechatStatus && !wechatStatus.installed && <div className="connector-state">
              <b>尚未安装本机连接器</b>
              <p>请在终端执行一次下面的命令，然后回到这里重新检测。</p>
              <code>{wechatStatus.setup_command}</code>
              <button onClick={() => run("正在重新检测…", detectWechat)} type="button">重新检测</button>
            </div>}

            {wechatStatus?.installed && !wechatStatus.ready && <div className="connector-state warning-state">
              <b>连接器已安装，但尚未初始化</b>
              <p>{wechatStatus.detail || "请保持电脑版微信登录，并在终端运行初始化命令。"}</p>
              <code>wx init</code>
              <small>macOS 可能要求额外系统权限。初始化属于敏感操作，网页不会替你自动执行。</small>
              <button onClick={() => run("正在重新检测…", detectWechat)} type="button">初始化完成，重新检测</button>
            </div>}

            {wechatStatus?.ready && <div className="connector-ready">
              <div className="ready-line"><span><i />已连接 {wechatStatus.version}</span><button onClick={() => run("正在刷新…", detectWechat)} type="button">刷新会话</button></div>
              {sessions.length === 0 ? <p className="warning">没有发现私人会话。</p> : <>
                <div className="field-grid">
                  <label>选择联系人
                    <select onChange={(event) => { setSelectedChat(event.target.value); setWechatPreview(null); setImportJob(null); }} value={selectedChat}>
                      {sessions.map((session) => <option key={session.name} value={session.name}>{session.name}</option>)}
                    </select>
                  </label>
                  <label>聊天中的你
                    <input onChange={(event) => setWechatSelf(event.target.value)} value={wechatSelf} />
                  </label>
                  <label>开始日期（选填）
                    <input onChange={(event) => setSince(event.target.value)} type="date" value={since} />
                  </label>
                  <label>结束日期（选填）
                    <input onChange={(event) => setUntil(event.target.value)} type="date" value={until} />
                  </label>
                </div>
                <button
                  className="primary"
                  disabled={!selectedChat || !wechatSelf.trim() || !!busy}
                  onClick={() => run("正在读取预览…", async () => {
                    setWechatPreview(await api<WechatPreview>("/wechat/preview", {
                      method: "POST", body: JSON.stringify(wechatRequest),
                    }));
                  })}
                  type="button"
                >只读预览</button>
              </>}
            </div>}

            {wechatPreview && <div className="wechat-preview">
              <div><b>{wechatPreview.message_count}</b><span>条最近文本消息预览，仅用于确认角色；下方按钮会导入日期范围内的全部记录</span></div>
              <ul>{wechatPreview.sample.map((message, index) => <li key={`${message.timestamp}-${index}`}><b>{message.speaker}</b><span>{message.text}</span></li>)}</ul>
              <button
                className="primary"
                disabled={!!busy || !!(importJob && !["completed", "failed"].includes(importJob.status))}
                onClick={() => run("正在启动导入…", async () => {
                  const value = await api<WechatImportJob>(`/projects/${project?.id}/wechat/import-jobs`, {
                    method: "POST",
                    body: JSON.stringify({
                      chat: selectedChat,
                      self_speaker: wechatSelf,
                      since: since || null,
                      until: until || null,
                      page_size: 1000,
                      analyze: true,
                    }),
                  });
                  setImportJob(value);
                })}
                type="button"
              >{importJob && !["completed", "failed"].includes(importJob.status)
                ? importJob.status === "merging"
                  ? "正在生成最终人格档案"
                  : "分析进行中，无需重复点击"
                : "导入全部并分析"}</button>
            </div>}

            {importJob && <div className={`import-progress ${importJob.status === "failed" ? "failed" : ""}`}>
              <div className="progress-heading">
                <b>{{
                  queued: "等待开始",
                  importing: "正在分页读取微信",
                  segmenting: "正在生成分析片段",
                  analyzing: "DeepSeek 正在分析",
                  merging: "正在生成最终人格档案",
                  completed: "完整导入与分析已完成",
                  failed: "任务暂停，可以续传",
                }[importJob.status]}</b>
                <span>{["analyzing", "merging"].includes(importJob.status) && importJob.chunk_count
                  ? `${Math.round(importJob.analyzed_chunk_count / importJob.chunk_count * 100)}%`
                  : `${importJob.imported_count} 条已保存`}</span>
              </div>
              <div className="progress-track">
                <i style={{width: ["completed", "merging"].includes(importJob.status) ? "100%" : importJob.status === "analyzing" && importJob.chunk_count ? `${Math.max(12, importJob.analyzed_chunk_count / importJob.chunk_count * 100)}%` : importJob.import_complete ? "12%" : "6%"}} />
              </div>
              {importJob.status === "analyzing" && importJob.chunk_count > 0 && <div className="progress-live" aria-live="polite">
                <b>正在处理第 {Math.min(importJob.analyzed_chunk_count + 1, importJob.chunk_count)} / {importJob.chunk_count} 段</b>
                <span>已完成 {importJob.analyzed_chunk_count} 段 · 剩余 {importJob.chunk_count - importJob.analyzed_chunk_count} 段</span>
                <span>预计剩余 {remainingTime(importJob) ?? "计算中"} · 最近更新 {new Date(importJob.updated_at).toLocaleTimeString("zh-CN", {hour12: false})}</span>
                <small>每个片段都要单独请求 DeepSeek，单段可能需要 10～30 秒；上方进度会自动刷新。</small>
              </div>}
              {importJob.status === "merging" && <div className="progress-live" aria-live="polite">
                <b>107 个片段已经全部分析完成</b>
                <span>正在去重并压缩人格特征、共同事件、长期记忆和回复范例。</span>
                <small>这是最后一步，不会重新读取聊天记录，也不会重新分析 107 个片段。</small>
              </div>}
              <div className="progress-meta">
                <span>读取偏移 {importJob.next_offset}</span>
                <span>{importJob.chunk_count} 个片段</span>
                <span>{importJob.analyzed_chunk_count} 个已分析</span>
                <span>{importJob.duplicate_count} 条重复</span>
              </div>
              {importJob.error && <p className="warning">{importJob.error}</p>}
              {importJob.status === "failed" && <button
                className="primary"
                disabled={!!busy}
                onClick={() => run("正在恢复…", async () => {
                  setImportJob(await api<WechatImportJob>(
                    `/projects/${project?.id}/wechat/import-jobs/${importJob.id}/resume`,
                    {method: "POST"},
                  ));
                })}
                type="button"
              >从上次位置继续</button>}
              {importJob.status === "completed" && <div className="reanalyze-box">
                <p>新版会提取情绪状态、情绪事件和跨场景人格，不再以回复模板为核心。</p>
                <button
                  className={confirmReanalysis ? "confirm-reanalysis" : ""}
                  disabled={!!busy}
                  onClick={() => {
                    if (!confirmReanalysis) {
                      setConfirmReanalysis(true);
                      return;
                    }
                    run("正在启动情绪人格分析…", async () => {
                      setImportJob(await api<WechatImportJob>(
                        `/projects/${project?.id}/wechat/import-jobs/${importJob.id}/reanalyze`,
                        {method: "POST"},
                      ));
                      setConfirmReanalysis(false);
                    });
                  }}
                  type="button"
                >{confirmReanalysis
                  ? `确认重新分析 ${importJob.chunk_count} 个片段（会消耗 API Token）`
                  : "使用新版情绪人格模型重新分析"}</button>
              </div>}
            </div>}
          </div>}

          {importMode === "paste" && <>
            <div className="format-tabs">
              <button className={format === "wechat_text" ? "selected" : ""} onClick={() => setFormat("wechat_text")} type="button">微信转换文本</button>
              <button className={format === "jsonl" ? "selected" : ""} onClick={() => setFormat("jsonl")} type="button">标准 JSONL</button>
            </div>
            <textarea
              className="import-box"
              disabled={!project}
              onChange={(e) => setContent(e.target.value)}
              rows={9}
              value={content}
            />
            <button
              className="primary"
              disabled={!project || !content.trim() || !!busy}
              onClick={() => run("正在分析…", async () => {
                const value = await api<ImportReport>(`/projects/${project?.id}/imports`, {
                  method: "POST", body: JSON.stringify({format, content}),
                });
                onReport(value);
                setTarget(value.participants[1]?.name ?? "");
                setSelf(value.participants[0]?.name ?? "");
              })}
              type="button"
            >分析聊天记录</button>
          </>}
        </div>
      </section>

      {report && <section className="step-card current wide">
        <div className="step-number">03</div>
        <div>
          <p className="eyebrow">确认身份</p>
          <h3>系统识别到了什么</h3>
          <div className="report-strip">
            <span><b>{report.inserted_count}</b> 有效消息</span>
            <span><b>{report.duplicate_count}</b> 重复</span>
            <span><b>{report.invalid_count}</b> 无法解析</span>
            <span><b>{report.privacy_findings.length}</b> 类隐私提示</span>
          </div>
          {!report.can_build && <p className="warning">检测到的参与者不是两位。V1 只允许双人聊天。</p>}
          <div className="field-grid">
            <label>要模拟的人
              <select onChange={(e) => setTarget(e.target.value)} value={target}>
                <option value="">请选择</option>
                {report.participants.map((item) => <option key={item.name}>{item.name}</option>)}
              </select>
            </label>
            <label>聊天中的你
              <select onChange={(e) => setSelf(e.target.value)} value={self}>
                <option value="">请选择</option>
                {report.participants.map((item) => <option key={item.name}>{item.name}</option>)}
              </select>
            </label>
          </div>
          <button
            className="primary"
            disabled={!report.can_build || !target || !self || target === self || !!busy}
            onClick={() => run("正在构建人格…", async () => {
              await api(`/projects/${project?.id}/identity`, {
                method: "PUT", body: JSON.stringify({target_speaker: target, user_speaker: self}),
              });
              onBuilt(await api<Persona>(`/projects/${project?.id}/persona/build`, {method: "POST"}));
            })}
            type="button"
          >确认并生成人格</button>
        </div>
      </section>}
      {project && <section className="danger-zone wide">
        <div>
          <p className="eyebrow">隐私与删除</p>
          <h3>永久删除这份人格档案</h3>
          <p>将同时删除原始消息、记忆、对话、反馈和全部人格版本。</p>
        </div>
        <button
          className={confirmDelete ? "confirm-delete" : ""}
          onClick={() => {
            if (!confirmDelete) {
              setConfirmDelete(true);
              return;
            }
            run("正在删除…", onDeleted);
          }}
          type="button"
        >{confirmDelete ? "再次点击，确认永久删除" : "删除人格档案"}</button>
      </section>}
    </div>
  );
}


function ChatView({persona, onCandidate, onMemoryCandidate, onError}: {
  persona: Persona; onCandidate: () => void; onMemoryCandidate: () => void;
  onError: (value: string) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([{
    id: "welcome", role: "assistant", content: `在。我是基于历史聊天生成的 ${persona.project.display_name}。`,
  }]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string>();
  const [correctingId, setCorrectingId] = useState("");
  const [idealReply, setIdealReply] = useState("");
  const [location, setLocation] = useState<RuntimeLocation>();
  const [locationState, setLocationState] = useState<"off" | "requesting" | "ready" | "denied">("off");
  const [runtimeContext, setRuntimeContext] = useState<RuntimeContext>();
  const conversationStorageKey = `persona-conversation:${persona.project.id}`;

  useEffect(() => {
    let cancelled = false;
    const savedId = window.sessionStorage.getItem(conversationStorageKey);
    setConversationId(undefined);
    setMessages([{
      id: "welcome", role: "assistant",
      content: `在。我是基于历史聊天生成的 ${persona.project.display_name}。`,
    }]);
    const restorePath = savedId
      ? `/projects/${persona.project.id}/conversations/${savedId}`
      : `/projects/${persona.project.id}/latest-conversation`;
    api<{conversation_id: string; messages: Message[]}>(
      restorePath,
    ).then((result) => {
      if (cancelled) return;
      setConversationId(result.conversation_id);
      window.sessionStorage.setItem(
        conversationStorageKey, result.conversation_id,
      );
      setMessages((current) => [current[0], ...result.messages]);
    }).catch((reason) => {
      window.sessionStorage.removeItem(conversationStorageKey);
      if (!(reason instanceof ApiError && reason.status === 404)) {
        onError(reason instanceof Error ? reason.message : "历史对话读取失败");
      }
    });
    return () => { cancelled = true; };
  }, [persona.project.id]);

  function enableLocation() {
    if (!navigator.geolocation) {
      setLocationState("denied");
      return;
    }
    setLocationState("requesting");
    navigator.geolocation.getCurrentPosition(
      ({coords}) => {
        setLocation({latitude: coords.latitude, longitude: coords.longitude});
        setLocationState("ready");
      },
      () => setLocationState("denied"),
      {enableHighAccuracy: false, maximumAge: 10 * 60 * 1000, timeout: 8000},
    );
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const content = input.trim();
    if (!content || sending) return;
    setMessages((current) => [...current, {id: crypto.randomUUID(), role: "user", content}]);
    setInput("");
    setSending(true);
    try {
      const result = await api<{
        reply: string; message_id: string; conversation_id: string;
        memory_candidate_id?: string;
        tone?: string;
        expression?: string;
        context: RuntimeContext;
      }>("/chat", {
        method: "POST",
        body: JSON.stringify({
          message: content,
          project_id: persona.project.id,
          conversation_id: conversationId,
          timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
          latitude: location?.latitude,
          longitude: location?.longitude,
        }),
      });
      setConversationId(result.conversation_id);
      window.sessionStorage.setItem(conversationStorageKey, result.conversation_id);
      setRuntimeContext(result.context);
      setMessages((current) => [...current, {
        id: result.message_id,
        role: "assistant",
        content: result.reply,
        tone: result.tone,
        expression: result.expression,
      }]);
      if (result.memory_candidate_id) onMemoryCandidate();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "发送失败");
    } finally { setSending(false); }
  }

  async function feedback(messageId: string, rating: "like" | "dislike", ideal?: string) {
    if (messageId === "welcome") return;
    try {
      await api(`/projects/${persona.project.id}/messages/${messageId}/feedback`, {
        method: "POST",
        body: JSON.stringify({
          rating,
          reason: rating === "dislike" ? "语气不够像" : "符合历史印象",
          ideal_reply: ideal || null,
        }),
      });
      setMessages((current) => current.map((item) => item.id === messageId ? {...item, feedback: rating} : item));
      onCandidate();
      setCorrectingId("");
      setIdealReply("");
    } catch (reason) { onError(reason instanceof Error ? reason.message : "反馈失败"); }
  }

  return (
    <div className="chat-layout">
      <section className="conversation">
        <div className="awareness-bar">
          <span>时间感知 · {runtimeContext?.period ?? "已开启"}</span>
          {runtimeContext?.life_state && <span>
            此刻 · {runtimeContext.life_state.activity}
          </span>}
          {runtimeContext?.weather && <span>
            天气感知 · {runtimeContext.weather.condition} {runtimeContext.weather.temperature_c}℃
          </span>}
          {locationState === "ready"
            ? <span>大致位置已授权</span>
            : <button disabled={locationState === "requesting"} onClick={enableLocation} type="button">
              {locationState === "requesting" ? "正在请求位置…" : locationState === "denied" ? "位置未授权，重试" : "开启天气与地点感知"}
            </button>}
        </div>
        <div className="date-divider"><span>今天</span></div>
        {messages.map((message) => <article className={`message ${message.role}`} key={message.id}>
          <span className="message-role">{message.role === "user" ? "你" : persona.project.display_name}</span>
          {message.role === "assistant" && (message.tone || message.expression) && <div className="nonverbal">
            {message.expression && <span>{message.expression}</span>}
            {message.tone && <i>{message.tone}</i>}
          </div>}
          <p>{message.content}</p>
          {message.role === "assistant" && message.id !== "welcome" && <div className="feedback" aria-label="评价回复">
            <button className={message.feedback === "like" ? "chosen" : ""} onClick={() => feedback(message.id, "like")} type="button">像</button>
            <button
              className={message.feedback === "dislike" || correctingId === message.id ? "chosen" : ""}
              onClick={() => setCorrectingId(message.id)}
              type="button"
            >不像</button>
          </div>}
          {correctingId === message.id && <form
            className="correction"
            onSubmit={(event) => {
              event.preventDefault();
              feedback(message.id, "dislike", idealReply).catch(() => undefined);
            }}
          >
            <label htmlFor={`correction-${message.id}`}>他更可能怎么说？（选填）</label>
            <div>
              <input id={`correction-${message.id}`} onChange={(event) => setIdealReply(event.target.value)} placeholder="输入更像的回复" value={idealReply} />
              <button type="submit">保存反馈</button>
            </div>
          </form>}
        </article>)}
        {sending && <article className="message assistant pending"><span className="message-role">{persona.project.display_name}</span><p>正在想……</p></article>}
      </section>
      <form className="composer" onSubmit={send}>
        <label htmlFor="chat-input">想说的话</label>
        <div className="composer-row">
          <textarea id="chat-input" maxLength={2000} onChange={(e) => setInput(e.target.value)} placeholder="比如：明天要面试了，有点慌" rows={2} value={input} />
          <button disabled={!input.trim() || sending} type="submit">发送</button>
        </div>
        <div className="composer-meta"><span>AI 回复不会自动成为训练证据</span><span>{input.length} / 2000</span></div>
      </form>
    </div>
  );
}


function LifeView({persona, onError}: {
  persona: Persona;
  onError: (value: string) => void;
}) {
  const [life, setLife] = useState<LifeSnapshot>();
  const [loading, setLoading] = useState(true);
  const [guidance, setGuidance] = useState("");
  const [guidanceDirty, setGuidanceDirty] = useState(false);
  const [generating, setGenerating] = useState(false);

  async function refresh() {
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      setLife(await api<LifeSnapshot>(
        `/projects/${persona.project.id}/life?timezone=${encodeURIComponent(timezone)}`,
      ));
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "虚拟生活读取失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(timer);
  }, [persona.project.id]);

  useEffect(() => {
    if (life && !guidanceDirty) setGuidance(life.guidance);
  }, [life?.guidance, guidanceDirty]);

  async function saveGuidance(event: FormEvent) {
    event.preventDefault();
    if (!guidance.trim() || generating) return;
    setGenerating(true);
    try {
      const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const result = await api<LifeSnapshot>(
        `/projects/${persona.project.id}/life/guidance`,
        {
          method: "PUT",
          body: JSON.stringify({guidance: guidance.trim(), timezone}),
        },
      );
      setLife(result);
      setGuidanceDirty(false);
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "今日生活生成失败");
      refresh();
    } finally {
      setGenerating(false);
    }
  }

  if (loading && !life) {
    return <div className="life-loading">正在推演此刻的生活状态……</div>;
  }
  if (!life) return null;
  const state = life.state;
  const meters = [
    ["精力", state.energy],
    ["饥饿", state.hunger],
    ["困意", state.sleepiness],
    ["健康", state.health],
    ["压力", state.stress],
  ] as const;
  return <div className="life-page">
    <form className="life-guidance" onSubmit={saveGuidance}>
      <div>
        <p className="eyebrow">Creative permission</p>
        <h3>虚拟生活设定</h3>
        <p>你写的是AI可以自由发挥的边界，不会被当作现实聊天证据。</p>
      </div>
      <textarea
        maxLength={5000}
        onChange={(event) => {
          setGuidance(event.target.value);
          setGuidanceDirty(true);
        }}
        placeholder="例如：她周一三五去公司上班，周二四居家办公。居家时可以摸鱼、陪小猫玩，也可能临时和朋友出去、唱K或逛街。她喜欢赖床，晚上通常睡得比较晚。允许AI在这些设定内自由安排普通日常。"
        rows={7}
        value={guidance}
      />
      <div className="life-guidance-actions">
        <span>生成今天时调用一次DeepSeek；之后刷新读取缓存，不重复消耗Token。</span>
        <button disabled={!guidance.trim() || generating} type="submit">
          {generating ? "正在生成今天…" : life.daily_plan ? "保存并重新生成今天" : "保存并生成今天"}
        </button>
      </div>
    </form>
    <section className="life-now">
      <div>
        <p className="eyebrow">Virtual life · {life.date}</p>
        <h3>{state.activity}</h3>
        <p>{state.location} · {state.mood}</p>
        {life.daily_plan?.summary && <p className="life-summary">{life.daily_plan.summary}</p>}
        {state.condition !== "未知" && <span className="life-condition">{state.condition}</span>}
      </div>
      <button onClick={refresh} type="button">推进到现在</button>
    </section>
    <p className="life-notice">{life.notice}</p>
    <section className="life-meters">
      {meters.map(([label, value]) => <article key={label}>
        <div><span>{label}</span><b>{value ?? "未知"}</b></div>
        <i>{value !== null && <span style={{width: `${value}%`}} />}</i>
      </article>)}
    </section>
    <section className="life-evidence">
      <p>{state.basis}</p>
      {[...state.evidence_message_ids, ...state.recent_evidence_message_ids].length > 0
        ? <EvidencePanel
          messageIds={[...state.evidence_message_ids, ...state.recent_evidence_message_ids]}
          onError={onError}
          projectId={persona.project.id}
        />
        : <small className="no-evidence">
          {state.activity_code === "creative_plan" ? "来源：你填写的虚拟生活设定" : "没有足够聊天证据"}
        </small>}
    </section>
    <section className="routine-profile">
      <div className="section-intro">
        <p className="eyebrow">Evidence-based routine</p>
        <h3>从聊天记录提取的作息线索</h3>
        <p>至少跨3个日期重复出现才会用于推演；其余只作为待确认线索。</p>
      </div>
      <div>
        {life.routine_profile.length === 0 && <p className="life-notice">没有找到明确的作息表达。</p>}
        {life.routine_profile.map((routine) => <article key={routine.kind}>
          <span>{routine.predictable && routine.usable
            ? `可用于推演 · ${routine.scope}`
            : routine.usable ? "条件性线索 · 不每日推演" : "证据不足"}</span>
          <h4>{routine.typical_time} · {routine.label}</h4>
          <p>{routine.basis}</p>
          <EvidencePanel
            messageIds={routine.evidence_message_ids}
            onError={onError}
            projectId={persona.project.id}
          />
        </article>)}
      </div>
    </section>
    <section className="life-timeline">
      <div className="section-intro">
        <p className="eyebrow">Today</p>
        <h3>{life.daily_plan ? "今天的虚拟日程" : "今天有证据支持的时间窗口"}</h3>
        <p>{life.daily_plan
          ? "由你的设定授权AI创作，全天只生成一次并缓存。"
          : "只展示有聊天证据支持的时间窗口，不调用DeepSeek。"}</p>
      </div>
      <ol>
        {life.events.map((event) => <li className={event.started_at === state.activity_started_at ? "current" : ""} key={event.id}>
          <time>{new Date(event.started_at).toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"})}</time>
          <div>
            <b>{event.title}</b>
            <span>{event.source === "user_guided_ai" ? "AI虚拟创作" : `${Math.round(event.confidence * 100)}%`} · {event.basis}</span>
          </div>
        </li>)}
      </ol>
    </section>
  </div>;
}


function PersonaView({persona, onChanged, onError}: {
  persona: Persona; onChanged: () => void; onError: (value: string) => void;
}) {
  const relationship = persona.version.relationship ?? {};
  const affectProfile = relationship.affect_profile ?? {};
  const emotionalEpisodes = relationship.emotional_episodes ?? [];
  const emotionalPatterns = relationship.emotional_patterns ?? [];
  const relationshipPatterns = relationship.relationship_patterns ?? [];
  const conflictPatterns = relationship.conflict_patterns ?? [];
  const needsAndBoundaries = relationship.needs_and_boundaries ?? [];
  const temporalChanges = relationship.temporal_changes ?? [];
  const hasEmotionalProfile = [
    emotionalPatterns, relationshipPatterns, conflictPatterns,
    needsAndBoundaries, temporalChanges,
  ].some((items) => items.length > 0)
    || Object.values(affectProfile).some(Boolean);

  return <div className="detail-grid">
    <section className="detail-card hero-card">
      <p className="eyebrow">Persona V{persona.version.version_number}</p>
      <h3>{persona.version.summary}</h3>
      <p>这是基于多条证据形成的情绪与性格画像；短期状态和稳定特征会分开判断。</p>
    </section>
    <section className="detail-card wide section-intro">
      <div>
        <p className="eyebrow">稳定性格</p>
        <h3>长期反复出现的行为倾向</h3>
        <p>每项都应由多条消息支持；反例会降低置信度，避免用一次行为定义一个人。</p>
      </div>
    </section>
    {persona.version.traits.map((trait, index) => (
      <TraitCard
        index={index}
        key={`${trait.name}-${index}`}
        onChanged={onChanged}
        onError={onError}
        projectId={persona.project.id}
        trait={trait}
      />
    ))}
    {!hasEmotionalProfile && <section className="detail-card wide emotional-empty">
      <div>
        <p className="eyebrow">情绪人格分析尚未生成</p>
        <h3>当前版本仍是旧版“说话风格”分析</h3>
        <p>重新分析后，这里会展示情绪触发、表达与恢复、关系互动、冲突修复、需求边界和时间变化。</p>
      </div>
    </section>}
    <AffectProfileSection profile={affectProfile} />
    <PatternSection eyebrow="情绪模式" title="什么会触发情绪，以及如何表达和恢复" items={emotionalPatterns} onError={onError} projectId={persona.project.id} />
    <PatternSection eyebrow="关系模式" title="对你的靠近、依赖、关心与回避" items={relationshipPatterns} onError={onError} projectId={persona.project.id} />
    <PatternSection eyebrow="冲突与修复" title="不舒服时如何反应，之后如何恢复关系" items={conflictPatterns} onError={onError} projectId={persona.project.id} />
    <PatternSection eyebrow="需求与边界" title="需要什么，也会拒绝什么" items={needsAndBoundaries} onError={onError} projectId={persona.project.id} />
    <PatternSection eyebrow="时间变化" title="早期与近期的情绪和关系变化" items={temporalChanges} onError={onError} projectId={persona.project.id} />
    <PatternSection eyebrow="代表情绪事件" title="事实、情绪过程和关系意义彼此分开" items={emotionalEpisodes} onError={onError} projectId={persona.project.id} />
  </div>;
}


function AffectProfileSection({profile}: {profile: AffectProfile}) {
  const entries = [
    ["情绪基线", profile.baseline],
    ["反应速度与强度", profile.reactivity],
    ["表达方式", profile.expression],
    ["调节方式", profile.regulation],
    ["恢复方式", profile.recovery],
    ["关系取向", profile.relationship_orientation],
    ["幽默方式", profile.humor_style],
  ].filter((entry): entry is [string, string] => Boolean(entry[1]));
  if (entries.length === 0) return null;
  return <section className="detail-card wide affect-profile">
    <div>
      <p className="eyebrow">Affective profile</p>
      <h3>这个人通常如何感受、表达和恢复</h3>
      <div className="affect-profile-grid">
        {entries.map(([label, value]) => <article key={label}>
          <small>{label}</small>
          <p>{value}</p>
        </article>)}
      </div>
    </div>
  </section>;
}


function TraitCard({projectId, trait, index, onChanged, onError}: {
  projectId: string; trait: Trait; index: number;
  onChanged: () => void; onError: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(trait.name);
  const [value, setValue] = useState(trait.value);
  const [confidence, setConfidence] = useState(trait.confidence);
  const [saving, setSaving] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!name.trim() || !value.trim() || saving) return;
    setSaving(true);
    try {
      await api(`/projects/${projectId}/persona/traits/${index}`, {
        method: "PUT",
        body: JSON.stringify({name: name.trim(), value: value.trim(), confidence}),
      });
      setEditing(false);
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "人格修正失败");
    } finally {
      setSaving(false);
    }
  }

  return <section className="detail-card editable-card">
    {editing ? <form className="inline-editor" onSubmit={save}>
      <label>特征名称
        <input maxLength={80} onChange={(event) => setName(event.target.value)} value={name} />
      </label>
      <label>性格与情绪结论
        <textarea maxLength={1000} onChange={(event) => setValue(event.target.value)} rows={4} value={value} />
      </label>
      <label>置信度：{Math.round(confidence * 100)}%
        <input max="1" min="0" onChange={(event) => setConfidence(Number(event.target.value))} step=".05" type="range" value={confidence} />
      </label>
      <div className="editor-actions">
        <button className="primary" disabled={saving} type="submit">{saving ? "保存中…" : "保存修正"}</button>
        <button onClick={() => setEditing(false)} type="button">取消</button>
      </div>
    </form> : <>
      <div className="confidence">{Math.round(trait.confidence * 100)}%</div>
      <p className="eyebrow">{trait.name}{trait.human_corrected ? " · 已人工修正" : ""}</p>
      <h3>{trait.value}</h3>
      {trait.evidence && !/^展示 \d+ 条代表证据$/.test(trait.evidence) && <p>{trait.evidence}</p>}
      <EvidencePanel messageIds={trait.source_message_ids ?? []} onError={onError} projectId={projectId} />
      <button className="edit-trigger" onClick={() => setEditing(true)} type="button">人工修正</button>
    </>}
  </section>;
}


function PatternSection({eyebrow, title, items, projectId, onError}: {
  eyebrow: string; title: string; items: AnalysisPattern[];
  projectId: string; onError: (value: string) => void;
}) {
  if (items.length === 0) return null;
  return <section className="detail-card wide pattern-section">
    <div>
      <p className="eyebrow">{eyebrow}</p>
      <h3>{title}</h3>
      <div className="pattern-list">
        {items.map((item, index) => {
          const heading = item.title || item.name || item.emotion || item.description
            || item.trigger || item.type || `模式 ${index + 1}`;
          const details = [
            item.triggers && `触发：${item.triggers}`,
            item.expression && `表达：${item.expression}`,
            item.regulation && `恢复：${item.regulation}`,
            item.reaction && `反应：${item.reaction}`,
            item.repair && `修复：${item.repair}`,
            item.toward_user && `对你：${item.toward_user}`,
            item.description && item.description !== heading && item.description,
            item.earlier && `早期：${item.earlier}`,
            item.recent && `近期：${item.recent}`,
            item.facts?.length && `事实：${item.facts.join("；")}`,
            item.initial_emotion && `初始情绪：${item.initial_emotion}`,
            item.peak_emotion && `情绪高点：${item.peak_emotion}`,
            item.coping && `应对：${item.coping}`,
            item.social_function && `表达目的：${item.social_function}`,
            item.relationship_signal && `关系信号：${item.relationship_signal}`,
          ].filter(Boolean);
          return <article key={`${heading}-${index}`}>
            <div>
              <b>{heading}</b>
              {typeof item.confidence === "number" && <span>{Math.round(item.confidence * 100)}%</span>}
            </div>
            {details.map((detail, detailIndex) => <p key={detailIndex}>{detail}</p>)}
            <EvidencePanel messageIds={item.evidence_message_ids ?? []} onError={onError} projectId={projectId} />
          </article>;
        })}
      </div>
    </div>
  </section>;
}


function EvidencePanel({projectId, messageIds, onError}: {
  projectId: string; messageIds: string[]; onError: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<EvidenceMessage[]>([]);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    if (messages.length === 0 && messageIds.length > 0) {
      setLoading(true);
      try {
        const result = await api<{messages: EvidenceMessage[]}>(`/projects/${projectId}/evidence`, {
          method: "POST",
          body: JSON.stringify({message_ids: messageIds}),
        });
        setMessages(result.messages);
      } catch (reason) {
        onError(reason instanceof Error ? reason.message : "证据读取失败");
        return;
      } finally {
        setLoading(false);
      }
    }
    setOpen(true);
  }

  if (messageIds.length === 0) {
    return <small className="no-evidence">没有可追溯的原始消息</small>;
  }
  return <div className="evidence-panel">
    <button className="evidence-toggle" disabled={loading} onClick={toggle} type="button">
      {loading ? "读取中…" : open ? "收起代表证据" : `查看 ${messageIds.length} 条代表证据`}
    </button>
    {open && <ol className="evidence-list">
      {messages.map((message) => <li key={message.id}>
        <div><b>{message.speaker}</b><time>{message.sent_at ? new Date(message.sent_at).toLocaleString("zh-CN") : "时间未知"}</time></div>
        <p>{message.text}</p>
      </li>)}
      {messages.length === 0 && <li className="missing-evidence">这些旧结论没有匹配到原始消息。</li>}
    </ol>}
  </div>;
}


function MemoryView({projectId, memories, candidates, onChanged, onError}: {
  projectId: string; memories: Memory[]; candidates: Memory[];
  onChanged: () => void; onError: (value: string) => void;
}) {
  async function approve(memoryId: string) {
    try {
      await api(`/projects/${projectId}/memories/${memoryId}/approve`, {method: "POST"});
      onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : "批准失败"); }
  }
  async function remove(memoryId: string) {
    try {
      await api(`/projects/${projectId}/memories/${memoryId}`, {method: "DELETE"});
      onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : "删除失败"); }
  }
  return <div className="memory-page">
    <div className="section-intro">
      <p className="eyebrow">Memory spine</p><h3>共同记忆脉络</h3>
      <p>每条记忆都能追溯到真实用户消息。AI 回复永远不会成为记忆来源。</p>
      {candidates.length > 0 && <div className="candidate-box">
        <b>{candidates.length} 条待确认记忆</b>
        {candidates.map((memory) => <article key={memory.id}>
          <p>{memory.content}</p>
          <div>
            <button onClick={() => approve(memory.id)} type="button">批准写入</button>
            <button onClick={() => remove(memory.id)} type="button">忽略</button>
          </div>
        </article>)}
      </div>}
    </div>
    <div className="memory-spine">
      {memories.length === 0 && <p className="empty">暂时没有抽取到共同记忆。</p>}
      {memories.map((memory) => (
        <MemoryCard
          key={memory.id}
          memory={memory}
          onChanged={onChanged}
          onError={onError}
          onRemove={() => remove(memory.id)}
          projectId={projectId}
        />
      ))}
    </div>
  </div>;
}


function MemoryCard({projectId, memory, onChanged, onRemove, onError}: {
  projectId: string; memory: Memory; onChanged: () => void;
  onRemove: () => void; onError: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [content, setContent] = useState(memory.content);
  const [eventDate, setEventDate] = useState(memory.event_date ?? "");
  const [importance, setImportance] = useState(memory.importance);
  const [saving, setSaving] = useState(false);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!content.trim() || saving) return;
    setSaving(true);
    try {
      await api(`/projects/${projectId}/memories/${memory.id}`, {
        method: "PUT",
        body: JSON.stringify({
          content: content.trim(),
          event_date: eventDate || null,
          importance,
        }),
      });
      setEditing(false);
      onChanged();
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : "记忆修改失败");
    } finally {
      setSaving(false);
    }
  }

  return <article>
    {editing ? <form className="inline-editor" onSubmit={save}>
      <label>记忆内容
        <textarea maxLength={2000} onChange={(event) => setContent(event.target.value)} rows={4} value={content} />
      </label>
      <label>发生日期
        <input onChange={(event) => setEventDate(event.target.value)} type="date" value={eventDate} />
      </label>
      <label>重要度：{Math.round(importance * 100)}%
        <input max="1" min="0" onChange={(event) => setImportance(Number(event.target.value))} step=".05" type="range" value={importance} />
      </label>
      <div className="editor-actions">
        <button className="primary" disabled={saving} type="submit">{saving ? "保存中…" : "保存修正"}</button>
        <button onClick={() => setEditing(false)} type="button">取消</button>
      </div>
    </form> : <>
      <time>{memory.event_date || "日期未知"} · 重要度 {Math.round(memory.importance * 100)}%</time>
      <p>{memory.content}</p>
      <EvidencePanel messageIds={memory.source_message_ids} onError={onError} projectId={projectId} />
      <div><button onClick={() => setEditing(true)} type="button">人工修正</button><button onClick={onRemove} type="button">删除</button></div>
    </>}
  </article>;
}


function VersionsView({projectId, versions, activeId, onChanged, onError}: {
  projectId: string; versions: Version[]; activeId?: string; onChanged: () => void; onError: (value: string) => void;
}) {
  async function publish() {
    try {
      await api(`/projects/${projectId}/candidates/publish`, {method: "POST", body: JSON.stringify({feedback_ids: []})});
      onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : "发布失败"); }
  }
  async function activate(versionId: string) {
    try {
      await api(`/projects/${projectId}/versions/${versionId}/activate`, {method: "POST"});
      onChanged();
    } catch (reason) { onError(reason instanceof Error ? reason.message : "回滚失败"); }
  }
  return <div className="version-page">
    <div className="section-intro">
      <p className="eyebrow">Controlled evolution</p><h3>人格不会偷偷改变</h3>
      <p>“像 / 不像”先成为候选反馈。你确认发布后，才生成一个可回滚的新版本。</p>
      <button className="primary" onClick={publish} type="button">发布全部候选反馈</button>
    </div>
    <div className="version-list">{versions.map((version) => <article className={version.id === activeId ? "active-version" : ""} key={version.id}>
      <span>V{version.version_number}</span>
      <div><h4>{version.summary}</h4><time>{new Date(version.created_at).toLocaleString("zh-CN")}</time></div>
      {version.id === activeId ? <b>当前版本</b> : <button onClick={() => activate(version.id)} type="button">切换到此版本</button>}
    </article>)}</div>
  </div>;
}
