const appUrl = process.env.NEXT_PUBLIC_RAILWAY_APP_URL || "";

export default function Home() {
  if (!appUrl) {
    return (
      <main className="setup">
        <section className="card">
          <h1>H5P Activity Generator</h1>
          <p>The frontend is ready, but the Railway application URL has not been configured.</p>
          <code>NEXT_PUBLIC_RAILWAY_APP_URL=https://your-app.up.railway.app</code>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <strong>H5P Activity Generator</strong>
          <span>AI activity builder</span>
        </div>
        <a href={appUrl} target="_blank" rel="noreferrer">Open full app</a>
      </header>
      <iframe
        className="appframe"
        src={appUrl}
        title="H5P Activity Generator"
        allow="clipboard-read; clipboard-write"
      />
    </main>
  );
}
