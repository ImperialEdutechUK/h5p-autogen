import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import generator_core as core
from h5p_browser_automation import AUTOMATION_VERSION, automate_h5p_com_import, check_h5p_com_connection

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", "/tmp/h5p_autogen_jobs"))
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
ARTIFACT_TTL_SECONDS = int(os.getenv("ARTIFACT_TTL_SECONDS", "7200"))
PUBLISH_DIR = ARTIFACT_DIR / "_publish_jobs"
PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
H5P_PUBLISH_TIMEOUT_SECONDS = int(os.getenv("H5P_PUBLISH_TIMEOUT_SECONDS", "300"))
RUNNING_PUBLISH_TASKS: set[asyncio.Task] = set()

app = FastAPI(title="H5P Activity Generator API", version="2.0.0")
origins_raw = os.getenv("FRONTEND_ORIGINS", "*").strip()
origins = [x.strip() for x in origins_raw.split(",") if x.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _cleanup_old_jobs() -> None:
    now = time.time()
    PUBLISH_DIR.mkdir(parents=True, exist_ok=True)
    for p in ARTIFACT_DIR.iterdir():
        try:
            if p == PUBLISH_DIR:
                for status_file in PUBLISH_DIR.glob("*.json"):
                    try:
                        if now - status_file.stat().st_mtime > ARTIFACT_TTL_SECONDS:
                            status_file.unlink(missing_ok=True)
                    except Exception:
                        pass
                continue
            if p.is_dir() and now - p.stat().st_mtime > ARTIFACT_TTL_SECONDS:
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass


def _read_upload(upload: UploadFile) -> bytes:
    data = upload.file.read()
    upload.file.seek(0)
    if not data:
        raise ValueError(f"{upload.filename or 'Uploaded file'} is empty.")
    return data


def _prepare_chunks(pdf_files: List[UploadFile]):
    chunks: List[core.ContentChunk] = []
    headings: List[str] = []
    term_freq: Dict[str, int] = {}

    for f in pdf_files:
        data = _read_upload(f)
        filename = f.filename or "source.pdf"
        file_chunks = core.extract_pdf_chunks_from_bytes(filename, data)
        chunks.extend(file_chunks)
        headings.extend(core.extract_pdf_headings_from_bytes(filename, data))
        for ch in file_chunks:
            for term in core._terms(ch.text):
                term_freq[term] = term_freq.get(term, 0) + 1

    if not chunks:
        raise ValueError("No readable text was found in the uploaded PDF files. Scanned PDFs need OCR first.")

    core._set_grounding_source(chunks)
    sorted_terms = sorted(term_freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    keywords = [t for t, _ in sorted_terms[:40]]
    return chunks, headings, keywords


def _qual_spec_text(qual_spec: Optional[UploadFile]) -> str:
    if not qual_spec:
        return ""
    data = _read_upload(qual_spec)
    chunks = core.extract_pdf_chunks_from_bytes(qual_spec.filename or "qualification.pdf", data)
    return core.join_chunks_for_prompt(chunks, max_chars=30000)


def _enriched_course(course_name: str, unit_name: str, qual_text: str) -> str:
    parts = [course_name.strip()]
    if unit_name.strip():
        parts.append(f"Unit: {unit_name.strip()}")
    if qual_text.strip():
        parts.append(f"Qualification Specification excerpt:\n{qual_text[:12000]}")
    return "\n".join(parts)


def _templates() -> Dict[str, str]:
    return core.discover_templates(str(TEMPLATES_DIR))


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "h5p-autogen-api",
        "llm_configured": bool(os.getenv("LLM_API_KEY")),
        "templates": len(_templates()),
        "h5p_automation_version": AUTOMATION_VERSION,
    }


@app.get("/api/activity-types")
def activity_types():
    templates = _templates()
    recommended = [x for x in core.BEST_H5P_TYPES if x in templates or x in {"Quiz", "Multiple Choice"}]
    return {"recommended": recommended, "all": sorted(templates.keys())}


@app.post("/api/suggest")
def suggest(
    pdfs: List[UploadFile] = File(...),
    course_name: str = Form(...),
    unit_name: str = Form(...),
    qualification_spec: Optional[UploadFile] = File(None),
):
    try:
        if not os.getenv("LLM_API_KEY"):
            raise ValueError("LLM_API_KEY is not configured on the backend.")
        chunks, _, _ = _prepare_chunks(pdfs)
        qual_text = _qual_spec_text(qualification_spec)
        small = core.choose_representative_chunks(chunks, max_pages=18)
        result = core.llm_suggest_activities(small, course_name.strip(), unit_name.strip(), qual_text)
        templates = _templates()
        recs = []
        for rec in result.get("recommendations") or []:
            if not isinstance(rec, dict):
                continue
            typ = rec.get("activity_type")
            if typ not in core.BEST_H5P_TYPES:
                continue
            rec = dict(rec)
            rec["template_ok"] = (typ in templates) if typ not in ("Quiz", "Multiple Choice") else ("Quiz" in templates)
            recs.append(rec)
        return {"recommendations": recs}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def _parse_options(raw: str) -> Dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception as exc:
        raise ValueError("options must be valid JSON.") from exc


def _generate_core(
    *,
    pdfs: List[UploadFile],
    course_name: str,
    unit_name: str,
    qualification_spec: Optional[UploadFile],
    activity_type: str,
    item_count: int,
    options: Dict[str, Any],
    poster_image: Optional[UploadFile],
) -> Dict[str, Any]:
    templates = _templates()
    typ = activity_type.strip()
    if not typ:
        raise ValueError("Activity type is required.")
    if typ in ("Quiz", "Multiple Choice"):
        if "Quiz" not in templates:
            raise ValueError("Missing templates/Quiz.h5p")
    elif typ not in templates:
        raise ValueError(f"Missing template: templates/{typ}.h5p")

    chunks, pdf_headings, pdf_keywords = _prepare_chunks(pdfs)
    qual_text = _qual_spec_text(qualification_spec)
    enriched_course = _enriched_course(course_name, unit_name, qual_text)

    run_n = max(1, int(item_count))
    if typ in core.LIMITED_Q_TYPES:
        max_q = core.LIMITED_Q_MAX_SINGLE_PDF if len(pdfs) == 1 else core.LIMITED_Q_MAX_MULTI_PDF
        run_n = max(core.LIMITED_Q_MIN, min(run_n, int(max_q)))

    job_id = uuid.uuid4().hex
    job_dir = ARTIFACT_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        qa_items: List[Dict[str, Any]] = []

        if typ == "Quiz":
            gen = core.call_llm_truefalse_statements(chunks, run_n, enriched_course)
            work = tmp / "quiz"
            core.unzip_h5p(templates["Quiz"], str(work))
            title = gen.get("title", f"True False Quiz - {course_name}")
            desc = gen.get("description", "Answer the True/False questions.")
            qa_items = core.build_question_set_truefalse(str(work), title, desc, gen.get("items", []), gen.get("overall_feedback"))

        elif typ == "Multiple Choice":
            gen = core.call_llm_multichoice_questions(chunks, run_n, enriched_course)
            work = tmp / "mcq"
            core.unzip_h5p(templates["Quiz"], str(work))
            title = gen.get("title", f"Multiple Choice Quiz - {course_name}")
            desc = gen.get("description", "Answer the multiple choice questions.")
            qa_items = core.build_question_set_multichoice(str(work), title, desc, gen.get("items", []), gen.get("overall_feedback"))

        elif typ == "Dialog Cards":
            work = tmp / "dialog"
            core.unzip_h5p(templates[typ], str(work))
            dialog_context = f"{course_name.strip()}\nUnit: {unit_name.strip()}"
            gen = core.generate_dialog_cards_strict(chunks, run_n, dialog_context)
            title = gen.get("title", f"Dialog Cards - {course_name}")
            qa_items = core.update_dialog_cards_template(
                str(work), title, gen.get("description", ""), gen.get("cards", []),
                course=course_name.strip(), pdf_headings=pdf_headings, pdf_keywords=pdf_keywords,
            )

        elif typ == "Dictation":
            work = tmp / "dictation"
            core.unzip_h5p(templates[typ], str(work))
            gen = core.call_llm_dictation(chunks, run_n, enriched_course)
            title = gen.get("title", f"Dictation - {course_name}")
            qa_items = core.update_dictation_template(
                str(work), title=title,
                description=gen.get("description", "Listen carefully and type what you hear."),
                sentences=gen.get("sentences", []), progress_callback=None,
            )

        elif typ == "Page":
            work = tmp / "page"
            core.unzip_h5p(templates[typ], str(work))
            gen = core.call_llm_page_content(chunks, n_sections=4, course=enriched_course)
            tf = core.call_llm_truefalse_statements(chunks, 1, enriched_course)
            mc = core.call_llm_multichoice_questions(chunks, 1, enriched_course)
            page_activities = []
            for it in (tf.get("items") or [])[:1]:
                page_activities.append({"type": "truefalse", "data": it, "evidence": it.get("evidence") or {}})
            for it in (mc.get("items") or [])[:1]:
                page_activities.append({"type": "multichoice", "data": it, "evidence": it.get("evidence") or {}})
            title = gen.get("title", f"Page - {course_name}")
            qa_items = core.update_page_template_with_images(
                str(work), title, gen.get("sections", []), course=course_name.strip(),
                pdf_headings=pdf_headings, pdf_keywords=pdf_keywords, activities=page_activities,
            )

        elif typ == "Course Presentation":
            work = tmp / "presentation"
            core.unzip_h5p(templates[typ], str(work))
            slides = int(options.get("content_slides", run_n))
            slides = max(3, min(slides, 7 if len(pdfs) == 1 else 11))
            gen = core.call_llm_course_presentation(chunks, n_slides=slides, course=enriched_course)
            act_type = str(options.get("activity_type", "Drag the Words"))
            q_count = max(0, min(int(options.get("activity_questions", 3)), 5))
            groups: Dict[str, List[Dict[str, Any]]] = {}
            if q_count:
                qd = core.call_llm_cp_activity_questions(chunks, act_type, q_count, enriched_course)
                groups[act_type] = qd.get("questions") or []
            title = gen.get("title", f"Course Presentation - {course_name}")
            qa_items = core.update_course_presentation_template_with_images(
                str(work), title=title, description=gen.get("description", ""),
                slides=gen.get("slides", []), course=course_name.strip(),
                pdf_headings=pdf_headings, pdf_keywords=pdf_keywords, activity_groups=groups,
            )

        elif typ == "Interactive Book":
            work = tmp / "book"
            core.unzip_h5p(templates[typ], str(work))
            pages = max(2, min(int(options.get("content_pages", run_n)), 10))
            gen = core.call_llm_interactive_book(chunks, n_chapters=pages, course=enriched_course)
            act_type = str(options.get("activity_type", "Fill in the Blanks"))
            q_count = max(0, min(int(options.get("activity_questions", 3)), 5))
            groups: Dict[str, List[Dict[str, Any]]] = {}
            if q_count:
                qd = core.call_llm_cp_activity_questions(chunks, act_type, q_count, enriched_course)
                groups[act_type] = qd.get("questions") or []
            title = gen.get("title", f"Interactive Book - {course_name}")
            qa_items = core.update_interactive_book_template_with_images(
                str(work), title=title, description=gen.get("description", ""),
                chapters=gen.get("chapters", []), course=course_name.strip(),
                pdf_headings=pdf_headings, pdf_keywords=pdf_keywords, activity_groups=groups,
            )

        elif typ in core.BUILTIN_TEXT_TYPES:
            meta = core.BUILTIN_TEXT_TYPES[typ]
            work = tmp / "text"
            core.unzip_h5p(templates[typ], str(work))
            if meta["mode"] == "dragtext":
                gen = core.call_llm_drag_words(chunks, run_n, enriched_course)
                textfield = core.make_dragtext_textfield(gen["items"])
                core.update_text_based_template(str(work), gen["title"], gen["description"], textfield, gen.get("overall_feedback"), meta["textfield_keys"])
                all_dis = []
                for it in gen.get("items", []):
                    all_dis.extend(it.get("distractors") or [])
                core.maybe_set_distractors(str(work), all_dis)
                title = gen["title"]
                qa_items = [{"label":"Drag the Words","content":it.get("sentence", ""),"expected":it.get("missing_word", ""),"evidence":it.get("evidence", {})} for it in gen.get("items", [])]
            elif meta["mode"] == "blanks":
                gen = core.call_llm_fill_blanks(chunks, run_n + 3, enriched_course)
                textfield = core.make_blanks_textfield(gen["items"], target_n=run_n)
                desc = (gen.get("description") or "").strip() or "Read each sentence and type the missing word."
                core.update_fill_in_the_blanks_template(str(work), gen["title"], desc, textfield, gen.get("overall_feedback"))
                title = gen["title"]
                qa_items = [{"label":f"Item {i+1}","content":f"{it.get('sentence','')} (answer: {it.get('answer','')})","evidence":it.get("evidence", {})} for i,it in enumerate(gen.get("items", [])[:run_n])]
            elif meta["mode"] == "markwords":
                gen = core.call_llm_mark_words(chunks, run_n, enriched_course)
                textfield = core.make_mark_words_textfield(gen["items"], target_n=run_n)
                core.update_text_based_template(str(work), gen["title"], gen["description"], textfield, gen.get("overall_feedback"), meta["textfield_keys"])
                title = gen["title"]
                qa_items = [{"label":f"Item {i+1}","content":f"{it.get('sentence','')} (marked: {it.get('marked_word','')})","evidence":it.get("evidence", {})} for i,it in enumerate(gen.get("items", []))]
            else:
                raise ValueError(f"Unsupported text activity mode: {meta['mode']}")

        elif typ == "Cornell Notes":
            work = tmp / "cornell"
            core.unzip_h5p(templates[typ], str(work))
            gen = core.call_llm_cornell_notes(chunks, enriched_course)
            poster_bytes = _read_upload(poster_image) if poster_image else None
            poster_ext = (poster_image.filename.rsplit(".",1)[-1].lower() if poster_image and poster_image.filename and "." in poster_image.filename else "jpg")
            title = gen.get("title") or f"Cornell Notes - {course_name}"
            core.update_cornell_notes_template(
                str(work), title=title, video_url=str(options.get("video_url", "")),
                gen_data=gen, poster_image_bytes=poster_bytes, poster_image_ext=poster_ext,
            )
            qa_items = [
                {"label":"Body","content":gen.get("body", ""),"evidence":{}},
                {"label":"Cue placeholder","content":gen.get("cue_placeholder", ""),"evidence":{}},
                {"label":"Notes placeholder","content":gen.get("notes_placeholder", ""),"evidence":{}},
                {"label":"Summary placeholder","content":gen.get("summary_placeholder", ""),"evidence":{}},
            ]

        elif typ == "Essay":
            work = tmp / "essay"
            core.unzip_h5p(templates[typ], str(work))
            gen = core.call_llm_essay(chunks, enriched_course)
            title = gen.get("title", f"Essay - {course_name}")
            qa_items = core.update_essay_template(str(work), title=title, description=gen.get("description", "Read the question and write your answer below."), essays=gen.get("essays", []))

        elif typ == "Summary":
            work = tmp / "summary"
            core.unzip_h5p(templates[typ], str(work))
            gen = core.call_llm_summary(chunks, run_n, enriched_course)
            title = gen["title"]
            core.update_summary_template(str(work), title, gen.get("groups", []), overall_feedback=gen.get("overall_feedback"), introduction=gen.get("introduction"))
            qa_items = [{"label":f"Group {i}","content":f"Correct: {g.get('correct_statement','')}\nIncorrect: {'; '.join(g.get('incorrect_statements', []))}","evidence":g.get("evidence", {})} for i,g in enumerate(gen.get("groups", []),1)]

        else:
            work = tmp / "generic"
            core.unzip_h5p(templates[typ], str(work))
            tpl_h5p = json.loads((work / "h5p.json").read_text(encoding="utf-8"))
            tpl_content = json.loads((work / "content" / "content.json").read_text(encoding="utf-8"))
            gen = core.call_llm_generic_patch(
                chunks=chunks, course_name=enriched_course, activity_type=typ,
                template_h5p_json=tpl_h5p, template_content_json=tpl_content, item_count=run_n,
            )
            title = gen["title"]
            core.update_h5p_title(str(work), title)
            core._save_json(str(work), "content/content.json", gen["patched_content_json"])
            qa_items = gen.get("qa_items", [])

        out_h5p = job_dir / f"{core.safe_filename(title)}.h5p"
        out_qa = job_dir / f"QA_{core.safe_filename(title)}.html"
        core.zip_dir_to_file(str(work), str(out_h5p))
        core.write_qa_report_html(str(out_qa), title, typ, qa_items)

    return {
        "job_id": job_id,
        "title": title,
        "activity_type": typ,
        "h5p_name": out_h5p.name,
        "qa_name": out_qa.name,
        "h5p_url": f"/api/jobs/{job_id}/h5p",
        "qa_url": f"/api/jobs/{job_id}/qa",
    }


@app.post("/api/generate")
def generate(
    pdfs: List[UploadFile] = File(...),
    course_name: str = Form(...),
    unit_name: str = Form(...),
    activity_type: str = Form(...),
    item_count: int = Form(4),
    options: str = Form("{}"),
    qualification_spec: Optional[UploadFile] = File(None),
    poster_image: Optional[UploadFile] = File(None),
):
    _cleanup_old_jobs()
    try:
        if not os.getenv("LLM_API_KEY"):
            raise ValueError("LLM_API_KEY is not configured on Railway.")
        return _generate_core(
            pdfs=pdfs, course_name=course_name, unit_name=unit_name,
            qualification_spec=qualification_spec, activity_type=activity_type,
            item_count=item_count, options=_parse_options(options), poster_image=poster_image,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/jobs/{job_id}/h5p")
def download_h5p(job_id: str):
    job_dir = ARTIFACT_DIR / job_id
    files = list(job_dir.glob("*.h5p")) if job_dir.exists() else []
    if not files:
        raise HTTPException(status_code=404, detail="Generated H5P file not found or expired.")
    return FileResponse(files[0], media_type="application/zip", filename=files[0].name)


@app.get("/api/jobs/{job_id}/qa")
def download_qa(job_id: str):
    job_dir = ARTIFACT_DIR / job_id
    files = list(job_dir.glob("QA_*.html")) if job_dir.exists() else []
    if not files:
        raise HTTPException(status_code=404, detail="QA report not found or expired.")
    return FileResponse(files[0], media_type="text/html", filename=files[0].name)


class H5PSendRequest(BaseModel):
    endpoint: Optional[str] = None
    token: Optional[str] = None
    auto_save: bool = True
    inspect_test_target: bool = True


@app.post("/api/jobs/{job_id}/send-to-h5p")
async def send_to_h5p(job_id: str, body: H5PSendRequest = H5PSendRequest()):
    """Send a generated .h5p to an editor.

    If a generic import endpoint is configured (via the request body or
    H5P_IMPORT_ENDPOINT), POST the package there directly. Otherwise fall
    back to the Playwright-based H5P.com automation (the same flow used by
    /publish-to-h5p), so this route still works out of the box using the
    H5P_CREATE_URL / H5P_USERNAME / H5P_PASSWORD variables documented in
    .env.railway.example instead of requiring a separate, undocumented
    H5P_IMPORT_ENDPOINT variable.
    """
    job_dir = ARTIFACT_DIR / job_id
    files = list(job_dir.glob("*.h5p")) if job_dir.exists() else []
    if not files:
        raise HTTPException(status_code=404, detail="Generated H5P file not found or expired.")

    endpoint = (body.endpoint or os.getenv("H5P_IMPORT_ENDPOINT", "")).strip()
    token = (body.token or os.getenv("H5P_IMPORT_TOKEN", "")).strip()

    if not endpoint:
        try:
            return await automate_h5p_com_import(
                str(files[0]),
                auto_save=body.auto_save,
                inspect_test_target=body.inspect_test_target,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    try:
        edit_url = core.send_h5p_to_editor(files[0].read_bytes(), files[0].name, endpoint, token)
        return {"edit_url": edit_url}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))

def _publish_status_path(publish_job_id: str) -> Path:
    return PUBLISH_DIR / f"{publish_job_id}.json"


def _write_publish_status(publish_job_id: str, payload: Dict[str, Any]) -> None:
    payload = dict(payload)
    payload["publish_job_id"] = publish_job_id
    payload["updated_at"] = time.time()
    path = _publish_status_path(publish_job_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_publish_status(publish_job_id: str) -> Dict[str, Any]:
    path = _publish_status_path(publish_job_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="H5P publish job not found or expired.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read H5P publish status: {exc}")


async def _run_h5p_publish_job(
    publish_job_id: str,
    h5p_path: str,
    auto_save: bool,
    inspect_test_target: bool,
) -> None:
    _write_publish_status(
        publish_job_id,
        {
            "status": "running",
            "message": "Uploading generated package to H5P.com...",
            "automation_version": AUTOMATION_VERSION,
        },
    )
    print(f"[H5P] Publish job {publish_job_id} started with {AUTOMATION_VERSION}.", flush=True)
    try:
        result = await asyncio.wait_for(
            automate_h5p_com_import(
                h5p_path,
                auto_save=auto_save,
                inspect_test_target=inspect_test_target,
            ),
            timeout=H5P_PUBLISH_TIMEOUT_SECONDS,
        )
        final_url = (
            result.get("url")
            or result.get("edit_url")
            or result.get("content_url")
        ) if isinstance(result, dict) else None
        _write_publish_status(
            publish_job_id,
            {
                "status": "completed",
                "message": "Published to H5P successfully.",
                "url": final_url,
                "result": result,
                "automation_version": AUTOMATION_VERSION,
            },
        )
        print(f"[H5P] Publish job {publish_job_id} completed: {final_url}", flush=True)
    except asyncio.TimeoutError:
        error = (
            f"H5P browser automation exceeded {H5P_PUBLISH_TIMEOUT_SECONDS} seconds. "
            "The browser did not complete the H5P import/save flow."
        )
        print(f"[H5P] Publish job {publish_job_id} FAILED: {error}", flush=True)
        _write_publish_status(
            publish_job_id,
            {
                "status": "failed",
                "message": "H5P publish timed out in the backend.",
                "error": error,
                "automation_version": AUTOMATION_VERSION,
            },
        )
    except asyncio.CancelledError:
        error = "H5P publish task was cancelled by the backend process before it completed."
        print(f"[H5P] Publish job {publish_job_id} CANCELLED.", flush=True)
        _write_publish_status(
            publish_job_id,
            {
                "status": "failed",
                "message": "H5P publish task was cancelled.",
                "error": error,
                "automation_version": AUTOMATION_VERSION,
            },
        )
    except Exception as exc:
        print(f"[H5P] Publish job {publish_job_id} FAILED: {exc}", flush=True)
        _write_publish_status(
            publish_job_id,
            {
                "status": "failed",
                "message": "H5P publish failed.",
                "error": str(exc),
                "automation_version": AUTOMATION_VERSION,
            },
        )


class H5PBrowserRequest(BaseModel):
    auto_save: bool = True
    inspect_test_target: bool = True


@app.get("/api/h5p/automation-check")
async def h5p_automation_check():
    """Check H5P.com authentication and inspect the configured testing URL."""
    try:
        return await check_h5p_com_connection()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs/{job_id}/publish-to-h5p", status_code=202)
async def publish_to_h5p(
    job_id: str,
    body: H5PBrowserRequest = H5PBrowserRequest(),
):
    """Start H5P.com publishing as a detached asyncio task and return immediately.

    Starlette BackgroundTasks run as part of the response lifecycle. A long browser
    automation job can therefore be cancelled by a proxy/request lifecycle even after
    the client has received the 202 response. Keeping a strong reference to a detached
    asyncio task avoids that failure mode while this web process remains alive.
    """
    job_dir = ARTIFACT_DIR / job_id
    files = list(job_dir.glob("*.h5p")) if job_dir.exists() else []
    if not files:
        raise HTTPException(status_code=404, detail="Generated H5P file not found or expired.")

    publish_job_id = uuid.uuid4().hex
    _write_publish_status(
        publish_job_id,
        {
            "status": "queued",
            "message": "H5P publish job queued.",
            "source_job_id": job_id,
            "automation_version": AUTOMATION_VERSION,
        },
    )

    task = asyncio.create_task(
        _run_h5p_publish_job(
            publish_job_id,
            str(files[0]),
            body.auto_save,
            body.inspect_test_target,
        ),
        name=f"h5p-publish-{publish_job_id}",
    )
    RUNNING_PUBLISH_TASKS.add(task)
    task.add_done_callback(RUNNING_PUBLISH_TASKS.discard)

    return {
        "publish_job_id": publish_job_id,
        "status": "queued",
        "status_url": f"/api/h5p/publish-jobs/{publish_job_id}",
        "automation_version": AUTOMATION_VERSION,
        "backend_timeout_seconds": H5P_PUBLISH_TIMEOUT_SECONDS,
    }


@app.get("/api/h5p/publish-jobs/{publish_job_id}")
def h5p_publish_status(publish_job_id: str):
    """Return the current state of a background H5P.com publish job."""
    return _read_publish_status(publish_job_id)
