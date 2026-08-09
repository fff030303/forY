import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from .database import connect, init_database, row_to_dict
from .deepseek_service import deepseek_reply, deepseek_status
from .import_pipeline import (
    create_import_job,
    get_import_job,
    list_import_jobs,
    prepare_import_job_resume,
    prepare_import_job_reanalysis,
    run_import_job,
)
from .runtime_context import build_runtime_context
from .life_simulation import (
    generate_daily_plan,
    get_life_snapshot,
    save_life_guidance,
)
from .schemas import (
    CandidatePublishRequest,
    ChatRequest,
    EvidenceRequest,
    FeedbackRequest,
    IdentityRequest,
    ImportRequest,
    LifeGuidanceUpdate,
    MemoryUpdate,
    ProjectCreate,
    TraitUpdate,
    WechatFullImportRequest,
    WechatHistoryRequest,
)
from .services import (
    build_persona,
    get_persona,
    import_messages,
    local_reply,
    new_id,
    now,
    require_project,
    should_propose_memory,
)
from .wechat_connector import (
    connector_status,
    fetch_private_history,
    import_wechat_history,
    list_private_sessions,
    preview_history,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_database()
    with connect() as db:
        db.execute(
            """UPDATE import_jobs SET status = 'failed',
            error = '服务曾在任务执行期间停止，请从上次位置继续',
            updated_at = ? WHERE status IN (
            'queued', 'importing', 'segmenting', 'analyzing', 'merging'
            )""",
            (now(),),
        )
    yield


app = FastAPI(title="Persona Companion API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def owner(x_user_id: Optional[str] = Header(default=None)) -> str:
    return x_user_id or "demo-user"


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": "1.0.0"}


@app.get("/ai/status")
def ai_status():
    return deepseek_status()


@app.post("/projects", status_code=201)
def create_project(request: ProjectCreate, x_user_id: Optional[str] = Header(default=None)):
    user_id = owner(x_user_id)
    if not request.consent_confirmed:
        raise HTTPException(status_code=422, detail="必须确认拥有聊天数据的使用授权")
    project_id = new_id()
    timestamp = now()
    with connect() as db:
        db.execute(
            """INSERT INTO projects (
            id, owner_user_id, display_name, relationship_type,
            consent_status, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'confirmed', 'draft', ?, ?)""",
            (
                project_id, user_id, request.display_name,
                request.relationship_type, timestamp, timestamp,
            ),
        )
    return require_project(project_id, user_id)


@app.get("/projects")
def list_projects(x_user_id: Optional[str] = Header(default=None)):
    with connect() as db:
        return [
            row_to_dict(row)
            for row in db.execute(
                "SELECT * FROM projects WHERE owner_user_id = ? ORDER BY created_at DESC",
                (owner(x_user_id),),
            ).fetchall()
        ]


@app.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, x_user_id: Optional[str] = Header(default=None)) -> Response:
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return Response(status_code=204)


@app.post("/projects/{project_id}/imports")
def create_import(
    project_id: str, request: ImportRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    return import_messages(project_id, owner(x_user_id), request.content, request.format)


@app.get("/wechat/status")
def wechat_status():
    return connector_status()


@app.get("/wechat/sessions")
def wechat_sessions(limit: int = 50):
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=422, detail="会话数量必须在 1 到 100 之间")
    return list_private_sessions(limit)


@app.post("/wechat/preview")
def preview_wechat(request: WechatHistoryRequest):
    messages = fetch_private_history(
        request.chat, request.self_speaker, request.since, request.until, request.limit
    )
    return preview_history(messages)


@app.post("/projects/{project_id}/wechat/import")
def import_from_wechat(
    project_id: str,
    request: WechatHistoryRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    messages = fetch_private_history(
        request.chat, request.self_speaker, request.since, request.until, request.limit
    )
    return import_wechat_history(project_id, user_id, messages)


@app.post("/projects/{project_id}/wechat/import-jobs", status_code=202)
def create_full_wechat_import(
    project_id: str,
    request: WechatFullImportRequest,
    background_tasks: BackgroundTasks,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    job = create_import_job(
        project_id=project_id,
        source_chat=request.chat,
        self_speaker=request.self_speaker,
        since=request.since,
        until=request.until,
        page_size=request.page_size,
        analyze=request.analyze,
    )
    background_tasks.add_task(run_import_job, job["id"])
    return job


@app.get("/projects/{project_id}/wechat/import-jobs")
def read_full_wechat_imports(
    project_id: str,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    return list_import_jobs(project_id)


@app.get("/projects/{project_id}/wechat/import-jobs/{job_id}")
def read_full_wechat_import(
    project_id: str,
    job_id: str,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    return get_import_job(project_id, job_id)


@app.post("/projects/{project_id}/wechat/import-jobs/{job_id}/resume", status_code=202)
def resume_full_wechat_import(
    project_id: str,
    job_id: str,
    background_tasks: BackgroundTasks,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    job = prepare_import_job_resume(project_id, job_id)
    background_tasks.add_task(run_import_job, job_id)
    return job


@app.post(
    "/projects/{project_id}/wechat/import-jobs/{job_id}/reanalyze",
    status_code=202,
)
def reanalyze_full_wechat_import(
    project_id: str,
    job_id: str,
    background_tasks: BackgroundTasks,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    job = prepare_import_job_reanalysis(project_id, job_id)
    background_tasks.add_task(run_import_job, job_id)
    return job


@app.put("/projects/{project_id}/identity")
def set_identity(
    project_id: str, request: IdentityRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    if request.target_speaker == request.user_speaker:
        raise HTTPException(status_code=422, detail="目标人物与当前用户不能相同")
    with connect() as db:
        speakers = {
            row[0]
            for row in db.execute(
                "SELECT DISTINCT speaker FROM source_messages WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        }
        if request.target_speaker not in speakers or request.user_speaker not in speakers:
            raise HTTPException(status_code=422, detail="身份必须来自已导入的参与者")
        if len(speakers) != 2:
            raise HTTPException(status_code=409, detail="V1 仅支持双人聊天")
        db.execute(
            """UPDATE projects SET target_speaker = ?, user_speaker = ?,
            status = 'identity_confirmed', updated_at = ? WHERE id = ?""",
            (request.target_speaker, request.user_speaker, now(), project_id),
        )
    return require_project(project_id, user_id)


@app.post("/projects/{project_id}/persona/build")
def create_persona(project_id: str, x_user_id: Optional[str] = Header(default=None)):
    return build_persona(project_id, owner(x_user_id))


@app.get("/projects/{project_id}/persona")
def read_persona(project_id: str, x_user_id: Optional[str] = Header(default=None)):
    return get_persona(project_id, owner(x_user_id))


@app.post("/projects/{project_id}/evidence")
def read_evidence(
    project_id: str, request: EvidenceRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    unique_ids = list(dict.fromkeys(request.message_ids))
    placeholders = ",".join("?" for _ in unique_ids)
    with connect() as db:
        rows = db.execute(
            f"""SELECT id, speaker, sent_at, normalized_text
            FROM source_messages
            WHERE project_id = ? AND id IN ({placeholders})""",
            [project_id, *unique_ids],
        ).fetchall()
    by_id = {row["id"]: row for row in rows}
    return {
        "messages": [
            {
                "id": message_id,
                "speaker": by_id[message_id]["speaker"],
                "sent_at": by_id[message_id]["sent_at"],
                "text": by_id[message_id]["normalized_text"],
            }
            for message_id in unique_ids if message_id in by_id
        ]
    }


@app.put("/projects/{project_id}/persona/traits/{trait_index}")
def update_trait(
    project_id: str, trait_index: int, request: TraitUpdate,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    project = require_project(project_id, user_id)
    if not project.get("active_version_id"):
        raise HTTPException(status_code=409, detail="当前没有可修改的人格版本")
    with connect() as db:
        row = db.execute(
            """SELECT traits_json FROM persona_versions
            WHERE id = ? AND project_id = ?""",
            (project["active_version_id"], project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="人格版本不存在")
        traits = json.loads(row["traits_json"])
        if trait_index < 0 or trait_index >= len(traits):
            raise HTTPException(status_code=404, detail="人格特征不存在")
        trait = traits[trait_index]
        if not trait.get("human_corrected"):
            trait["ai_original"] = {
                "name": trait.get("name", ""),
                "value": trait.get("value", ""),
                "confidence": trait.get("confidence", 0),
            }
        trait.update({
            "name": request.name,
            "value": request.value,
            "confidence": request.confidence,
            "human_corrected": True,
            "corrected_at": now(),
        })
        db.execute(
            "UPDATE persona_versions SET traits_json = ? WHERE id = ?",
            (
                json.dumps(traits, ensure_ascii=False),
                project["active_version_id"],
            ),
        )
    return get_persona(project_id, user_id)


@app.get("/projects/{project_id}/versions")
def list_versions(project_id: str, x_user_id: Optional[str] = Header(default=None)):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        return [
            row_to_dict(row)
            for row in db.execute(
                "SELECT * FROM persona_versions WHERE project_id = ? ORDER BY version_number DESC",
                (project_id,),
            ).fetchall()
        ]


@app.post("/projects/{project_id}/versions/{version_id}/activate")
def activate_version(
    project_id: str, version_id: str,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        found = db.execute(
            "SELECT id FROM persona_versions WHERE id = ? AND project_id = ?",
            (version_id, project_id),
        ).fetchone()
        if not found:
            raise HTTPException(status_code=404, detail="人格版本不存在")
        db.execute(
            "UPDATE projects SET active_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now(), project_id),
        )
    return get_persona(project_id, user_id)


@app.put("/projects/{project_id}/memories/{memory_id}")
def update_memory(
    project_id: str, memory_id: str, request: MemoryUpdate,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        cursor = db.execute(
            """UPDATE memories SET content = ?, importance = ?, event_date = ?, updated_at = ?
            WHERE id = ? AND project_id = ? AND status = 'active'""",
            (
                request.content, request.importance,
                request.event_date.isoformat() if request.event_date else None,
                now(), memory_id, project_id,
            ),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="记忆不存在")
        row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return row_to_dict(row)


@app.delete("/projects/{project_id}/memories/{memory_id}", status_code=204)
def delete_memory(
    project_id: str, memory_id: str,
    x_user_id: Optional[str] = Header(default=None),
) -> Response:
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        cursor = db.execute(
            "DELETE FROM memories WHERE id = ? AND project_id = ?",
            (memory_id, project_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="记忆不存在")
    return Response(status_code=204)


@app.post("/projects/{project_id}/memories/{memory_id}/approve")
def approve_memory(
    project_id: str, memory_id: str,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        cursor = db.execute(
            """UPDATE memories SET status = 'active', updated_at = ?
            WHERE id = ? AND project_id = ? AND status = 'candidate'""",
            (now(), memory_id, project_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="候选记忆不存在")
        row = db.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return row_to_dict(row)


@app.post("/chat")
def chat(request: ChatRequest, x_user_id: Optional[str] = Header(default=None)):
    user_id = owner(x_user_id)
    if not request.project_id:
        return {"reply": "你好呀", "message_id": None, "conversation_id": None}
    persona = get_persona(request.project_id, user_id)
    conversation_id = request.conversation_id or new_id()
    conversation_exists = None
    conversation_history = []
    if request.conversation_id:
        with connect() as db:
            conversation_exists = db.execute(
                "SELECT id FROM conversations WHERE id = ? AND project_id = ?",
                (conversation_id, request.project_id),
            ).fetchone()
            if not conversation_exists:
                raise HTTPException(status_code=404, detail="会话不存在")
            rows = db.execute(
                """SELECT role, content FROM chat_messages
                WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 20""",
                (conversation_id,),
            ).fetchall()
        conversation_history = [dict(row) for row in reversed(rows)]
    timestamp = now()
    user_message_id = new_id()
    assistant_message_id = new_id()
    runtime_context = build_runtime_context(
        request.timezone, request.latitude, request.longitude
    )
    life = get_life_snapshot(request.project_id, request.timezone)
    runtime_context["virtual_life"] = {
        key: life["state"][key]
        for key in (
            "activity", "location", "mood", "condition", "energy",
            "hunger", "sleepiness", "health", "stress",
        )
    }
    generated = deepseek_reply(
        request.message,
        persona["project"],
        persona["examples"],
        persona["memories"],
        persona["version"],
        runtime_context,
        conversation_history,
    )
    if generated:
        reply = generated["reply"]
        tone = generated["tone"]
        expression = generated["expression"]
    else:
        reply = local_reply(request.message, persona["project"], persona["examples"])
        tone = ""
        expression = ""
    with connect() as db:
        if not conversation_exists:
            db.execute(
                "INSERT INTO conversations (id, project_id, created_at) VALUES (?, ?, ?)",
                (conversation_id, request.project_id, timestamp),
            )
        db.executemany(
            """INSERT INTO chat_messages (
            id, conversation_id, role, content, source, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (user_message_id, conversation_id, "user", request.message, "human", timestamp),
                (assistant_message_id, conversation_id, "assistant", reply, "generated", timestamp),
            ],
        )
        memory_candidate_id = None
        if should_propose_memory(request.message):
            memory_candidate_id = new_id()
            db.execute(
                """INSERT INTO memories (
                id, project_id, version_id, content, importance,
                source_message_ids_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0.5, ?, 'candidate', ?, ?)""",
                (
                    memory_candidate_id,
                    request.project_id,
                    persona["version"]["id"],
                    request.message,
                    json.dumps([user_message_id]),
                    timestamp,
                    timestamp,
                ),
            )
    return {
        "reply": reply, "message_id": assistant_message_id,
        "conversation_id": conversation_id,
        "memory_used": [item["id"] for item in persona["memories"][:3]],
        "memory_candidate_id": memory_candidate_id,
        "tone": tone,
        "expression": expression,
        "context": {
            "period": runtime_context["period"],
            "weather": runtime_context.get("weather"),
            "location_authorized": runtime_context["location_authorized"],
            "life_state": life["state"],
        },
    }


@app.get("/projects/{project_id}/conversations/{conversation_id}")
def read_conversation(
    project_id: str, conversation_id: str,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        conversation = db.execute(
            "SELECT id FROM conversations WHERE id = ? AND project_id = ?",
            (conversation_id, project_id),
        ).fetchone()
        if not conversation:
            raise HTTPException(status_code=404, detail="会话不存在")
        rows = db.execute(
            """SELECT id, role, content FROM chat_messages
            WHERE conversation_id = ? ORDER BY rowid""",
            (conversation_id,),
        ).fetchall()
    return {
        "conversation_id": conversation_id,
        "messages": [row_to_dict(row) for row in rows],
    }


@app.get("/projects/{project_id}/latest-conversation")
def read_latest_conversation(
    project_id: str, x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    with connect() as db:
        conversation = db.execute(
            """SELECT c.id FROM conversations c
            JOIN chat_messages cm ON cm.conversation_id = c.id
            WHERE c.project_id = ? GROUP BY c.id
            ORDER BY MAX(cm.rowid) DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if not conversation:
            raise HTTPException(status_code=404, detail="还没有历史会话")
        rows = db.execute(
            """SELECT id, role, content FROM chat_messages
            WHERE conversation_id = ? ORDER BY rowid""",
            (conversation["id"],),
        ).fetchall()
    return {
        "conversation_id": conversation["id"],
        "messages": [row_to_dict(row) for row in rows],
    }


@app.get("/projects/{project_id}/life")
def read_life(
    project_id: str,
    timezone: Optional[str] = None,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    return get_life_snapshot(project_id, timezone)


@app.put("/projects/{project_id}/life/guidance")
def update_life_guidance(
    project_id: str,
    request: LifeGuidanceUpdate,
    x_user_id: Optional[str] = Header(default=None),
):
    require_project(project_id, owner(x_user_id))
    save_life_guidance(project_id, request.guidance)
    generate_daily_plan(project_id, request.timezone)
    return get_life_snapshot(project_id, request.timezone)


@app.post("/projects/{project_id}/messages/{message_id}/feedback", status_code=201)
def create_feedback(
    project_id: str, message_id: str, request: FeedbackRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    require_project(project_id, user_id)
    feedback_id = new_id()
    with connect() as db:
        message = db.execute(
            """SELECT cm.id FROM chat_messages cm
            JOIN conversations c ON c.id = cm.conversation_id
            WHERE cm.id = ? AND c.project_id = ? AND cm.role = 'assistant'""",
            (message_id, project_id),
        ).fetchone()
        if not message:
            raise HTTPException(status_code=404, detail="AI 回复不存在")
        db.execute(
            """INSERT INTO feedback (
            id, project_id, message_id, rating, reason,
            ideal_reply, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'candidate', ?)""",
            (
                feedback_id, project_id, message_id, request.rating,
                request.reason, request.ideal_reply, now(),
            ),
        )
    return {
        "id": feedback_id, "status": "candidate",
        "note": "反馈只进入候选区，不会直接改写已发布人格",
    }


@app.post("/projects/{project_id}/candidates/publish")
def publish_candidate(
    project_id: str, request: CandidatePublishRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    user_id = owner(x_user_id)
    current = get_persona(project_id, user_id)
    with connect() as db:
        if request.feedback_ids:
            placeholders = ",".join("?" for _ in request.feedback_ids)
            feedback_rows = db.execute(
                f"""SELECT * FROM feedback WHERE project_id = ?
                AND id IN ({placeholders}) AND status = 'candidate'""",
                [project_id, *request.feedback_ids],
            ).fetchall()
        else:
            feedback_rows = db.execute(
                "SELECT * FROM feedback WHERE project_id = ? AND status = 'candidate'",
                (project_id,),
            ).fetchall()
        if not feedback_rows:
            raise HTTPException(status_code=409, detail="没有可发布的候选反馈")
        next_number = db.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 FROM persona_versions WHERE project_id = ?",
            (project_id,),
        ).fetchone()[0]
        version_id = new_id()
        traits = current["version"]["traits"]
        traits.append({
            "name": "用户校准", "value": f"已吸收 {len(feedback_rows)} 条人工反馈",
            "confidence": 1, "evidence": "用户主动确认的反馈",
        })
        db.execute(
            """INSERT INTO persona_versions (
            id, project_id, version_number, status, summary,
            traits_json, relationship_json, created_at
            ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?)""",
            (
                version_id, project_id, next_number,
                f"从 V{current['version']['version_number']} 吸收人工反馈",
                json.dumps(traits, ensure_ascii=False),
                json.dumps(current["version"]["relationship"], ensure_ascii=False), now(),
            ),
        )
        db.execute(
            """INSERT INTO dialogue_examples (
            id, project_id, version_id, context_text, reply_text,
            source_message_ids_json, created_at
            )
            SELECT lower(hex(randomblob(16))), project_id, ?, context_text, reply_text,
            source_message_ids_json, ?
            FROM dialogue_examples WHERE project_id = ? AND version_id = ?""",
            (version_id, now(), project_id, current["version"]["id"]),
        )
        for row in feedback_rows:
            if row["ideal_reply"]:
                db.execute(
                    """INSERT INTO dialogue_examples (
                    id, project_id, version_id, context_text, reply_text,
                    source_message_ids_json, created_at
                    ) VALUES (?, ?, ?, '用户校准场景', ?, '[]', ?)""",
                    (new_id(), project_id, version_id, row["ideal_reply"], now()),
                )
        ids = [row["id"] for row in feedback_rows]
        placeholders = ",".join("?" for _ in ids)
        db.execute(
            f"UPDATE feedback SET status = 'published' WHERE id IN ({placeholders})", ids
        )
        db.execute(
            "UPDATE projects SET active_version_id = ?, updated_at = ? WHERE id = ?",
            (version_id, now(), project_id),
        )
    return get_persona(project_id, user_id)
