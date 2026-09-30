"use client";

import { useEffect, useMemo, useState } from "react";

const API = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

function errorText(err) {
  if (!err) return "Unknown error";
  if (typeof err === "string") return err;
  return err.detail || err.message || JSON.stringify(err);
}

export default function Home() {
  const [pdfs, setPdfs] = useState([]);
  const [qualification, setQualification] = useState(null);
  const [courseName, setCourseName] = useState("");
  const [unitName, setUnitName] = useState("");
  const [activityTypes, setActivityTypes] = useState([]);
  const [allTypes, setAllTypes] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [activityType, setActivityType] = useState("");
  const [itemCount, setItemCount] = useState(4);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [videoUrl, setVideoUrl] = useState("");
  const [poster, setPoster] = useState(null);
  const [contentSlides, setContentSlides] = useState(4);
  const [activityQuestions, setActivityQuestions] = useState(3);
  const [embeddedActivityType, setEmbeddedActivityType] = useState("Drag the Words");

  useEffect(() => {
    if (!API) return;
    fetch(`${API}/api/activity-types`)
      .then((r) => r.json().then((j) => ({ ok: r.ok, j })))
      .then(({ ok, j }) => {
        if (!ok) throw new Error(errorText(j));
        setActivityTypes(j.recommended || []);
        setAllTypes(j.all || []);
      })
      .catch((e) => setError(`Could not load activity types: ${e.message}`));
  }, []);

  const canSubmit = useMemo(
    () => API && pdfs.length > 0 && courseName.trim() && unitName.trim(),
    [pdfs, courseName, unitName]
  );

  function baseForm() {
    const fd = new FormData();
    pdfs.forEach((f) => fd.append("pdfs", f));
    fd.append("course_name", courseName.trim());
    fd.append("unit_name", unitName.trim());
    if (qualification) fd.append("qualification_spec", qualification);
    return fd;
  }

  async function suggest() {
    setError("");
    setResult(null);
    if (!canSubmit) {
      setError("Please upload at least one PDF and enter the course and unit names.");
      return;
    }
    setBusy(true);
    setStatus("Analysing the PDFs and generating H5P suggestions...");
    try {
      const fd = baseForm();
      const r = await fetch(`${API}/api/suggest`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(errorText(j));
      const recs = j.recommendations || [];
      setSuggestions(recs);
      if (recs[0]?.activity_type) {
        setActivityType(recs[0].activity_type);
        setItemCount(recs[0].suggested_item_count || 4);
      }
      setStatus(recs.length ? "Suggestions are ready." : "No suggestions were returned.");
    } catch (e) {
      setError(e.message);
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  function generationOptions() {
    if (activityType === "Course Presentation") {
      return {
        content_slides: Number(contentSlides),
        activity_type: embeddedActivityType,
        activity_questions: Number(activityQuestions),
      };
    }
    if (activityType === "Interactive Book") {
      return {
        content_pages: Number(contentSlides),
        activity_type: embeddedActivityType,
        activity_questions: Number(activityQuestions),
      };
    }
    if (activityType === "Cornell Notes") {
      return { video_url: videoUrl.trim() };
    }
    return {};
  }

  async function generate() {
    setError("");
    setResult(null);
    if (!canSubmit || !activityType) {
      setError("Complete the course details, upload a PDF and choose an activity type.");
      return;
    }
    setBusy(true);
    setStatus(`Generating ${activityType}. This can take a few minutes...`);
    try {
      const fd = baseForm();
      fd.append("activity_type", activityType);
      fd.append("item_count", String(itemCount));
      fd.append("options", JSON.stringify(generationOptions()));
      if (poster) fd.append("poster_image", poster);
      const r = await fetch(`${API}/api/generate`, { method: "POST", body: fd });
      const j = await r.json();
      if (!r.ok) throw new Error(errorText(j));
      setResult(j);
      setStatus("H5P activity generated successfully.");
    } catch (e) {
      setError(e.message);
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  async function sendToH5P() {
    if (!result?.job_id) return;
    setBusy(true);
    setError("");
    setStatus("Sending the generated package to H5P...");
    try {
      const r = await fetch(`${API}/api/jobs/${result.job_id}/send-to-h5p`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(errorText(j));
      setResult((old) => ({ ...old, edit_url: j.edit_url }));
      setStatus("H5P received the activity.");
    } catch (e) {
      setError(e.message);
      setStatus("");
    } finally {
      setBusy(false);
    }
  }

  if (!API) {
    return (
      <main className="wrap">
        <section className="card narrow">
          <h1>H5P Activity Generator</h1>
          <p>The frontend is ready, but the Railway API URL is missing.</p>
          <code>NEXT_PUBLIC_API_URL=https://your-api.up.railway.app</code>
        </section>
      </main>
    );
  }

  return (
    <main className="wrap">
      <section className="hero">
        <div>
          <span className="eyebrow">AI H5P BUILDER</span>
          <h1>H5P Activity Generator</h1>
          <p>Upload course content, get AI recommendations and generate a downloadable H5P package without Streamlit.</p>
        </div>
        <a className="health" href={`${API}/health`} target="_blank" rel="noreferrer">API status</a>
      </section>

      <section className="card">
        <h2>1. Course content</h2>
        <div className="grid two">
          <label>
            <span>Course name *</span>
            <input value={courseName} onChange={(e) => setCourseName(e.target.value)} placeholder="e.g. Level 5 Diploma in Teaching" />
          </label>
          <label>
            <span>Unit name *</span>
            <input value={unitName} onChange={(e) => setUnitName(e.target.value)} placeholder="e.g. Unit 1: Personal Development" />
          </label>
        </div>
        <label className="upload">
          <span>Teaching PDF file(s) *</span>
          <input type="file" accept="application/pdf" multiple onChange={(e) => setPdfs(Array.from(e.target.files || []))} />
          <small>{pdfs.length ? `${pdfs.length} file(s) selected` : "Select one or more text-based PDFs"}</small>
        </label>
        <label className="upload">
          <span>Qualification specification (optional)</span>
          <input type="file" accept="application/pdf" onChange={(e) => setQualification(e.target.files?.[0] || null)} />
        </label>
        <button className="secondary" disabled={busy || !canSubmit} onClick={suggest}>Suggest H5P types</button>
      </section>

      {suggestions.length > 0 && (
        <section className="card">
          <h2>2. AI recommendations</h2>
          <div className="suggestions">
            {suggestions.map((s, i) => (
              <button
                key={`${s.activity_type}-${i}`}
                className={`suggestion ${activityType === s.activity_type ? "selected" : ""}`}
                onClick={() => {
                  setActivityType(s.activity_type);
                  setItemCount(s.suggested_item_count || 4);
                }}
              >
                <div><strong>{s.activity_type}</strong><span>{s.score_0_to_5 ?? ""}/5</span></div>
                <p>{s.why}</p>
                {!s.template_ok && <em>Template missing</em>}
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="card">
        <h2>3. Generate activity</h2>
        <div className="grid two">
          <label>
            <span>Activity type *</span>
            <select value={activityType} onChange={(e) => setActivityType(e.target.value)}>
              <option value="">Choose an activity</option>
              {(allTypes.length ? allTypes : activityTypes).map((t) => <option key={t} value={t}>{t}</option>)}
              {!allTypes.includes("Quiz") && <option value="Quiz">Quiz</option>}
              {!allTypes.includes("Multiple Choice") && <option value="Multiple Choice">Multiple Choice</option>}
            </select>
          </label>
          <label>
            <span>Items / questions</span>
            <input type="number" min="1" max="30" value={itemCount} onChange={(e) => setItemCount(Number(e.target.value))} />
          </label>
        </div>

        {(activityType === "Course Presentation" || activityType === "Interactive Book") && (
          <div className="subpanel">
            <div className="grid three">
              <label>
                <span>{activityType === "Course Presentation" ? "Content slides" : "Content pages"}</span>
                <input type="number" min="2" max="11" value={contentSlides} onChange={(e) => setContentSlides(Number(e.target.value))} />
              </label>
              <label>
                <span>Embedded activity</span>
                <select value={embeddedActivityType} onChange={(e) => setEmbeddedActivityType(e.target.value)}>
                  <option>Drag the Words</option>
                  <option>Fill in the Blanks</option>
                  <option>True/False</option>
                  <option>Mark the Words</option>
                </select>
              </label>
              <label>
                <span>Activity questions</span>
                <input type="number" min="0" max="5" value={activityQuestions} onChange={(e) => setActivityQuestions(Number(e.target.value))} />
              </label>
            </div>
          </div>
        )}

        {activityType === "Cornell Notes" && (
          <div className="subpanel">
            <label>
              <span>Vimeo / YouTube URL (optional)</span>
              <input value={videoUrl} onChange={(e) => setVideoUrl(e.target.value)} placeholder="https://vimeo.com/..." />
            </label>
            <label className="upload">
              <span>Poster image (optional)</span>
              <input type="file" accept="image/png,image/jpeg" onChange={(e) => setPoster(e.target.files?.[0] || null)} />
            </label>
          </div>
        )}

        <button className="primary" disabled={busy || !canSubmit || !activityType} onClick={generate}>
          {busy ? "Working..." : "Generate H5P"}
        </button>
      </section>

      {(status || error) && (
        <section className={`notice ${error ? "error" : "ok"}`}>
          {error || status}
        </section>
      )}

      {result && (
        <section className="card result">
          <h2>Activity ready</h2>
          <p><strong>{result.title}</strong></p>
          <div className="actions">
            <a className="primary link" href={`${API}${result.h5p_url}`}>Download .h5p</a>
            <a className="secondary link" href={`${API}${result.qa_url}`} target="_blank" rel="noreferrer">Open QA report</a>
            <button className="secondary" onClick={sendToH5P} disabled={busy}>Send to H5P</button>
            {result.edit_url && <a className="secondary link" href={result.edit_url} target="_blank" rel="noreferrer">Open H5P Editor</a>}
          </div>
        </section>
      )}
    </main>
  );
}
