const timer = document.querySelector('#countdown');
if (timer) {
  const expiresAt = new Date(timer.dataset.expires + 'Z').getTime();
  const update = () => {
    const seconds = Math.max(0, Math.floor((expiresAt - Date.now()) / 1000));
    timer.textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    if (seconds === 0) location.reload();
  };
  update();
  setInterval(update, 1000);

  const refreshQr = async () => {
    try {
      const response = await fetch(window.QR_REFRESH_URL, {cache: 'no-store'});
      if (!response.ok) { location.reload(); return; }
      const data = await response.json();
      if (!data.open) { location.reload(); return; }
      document.querySelector('#live-qr').src = data.qr_data;
      const link = document.querySelector('#scan-url');
      link.href = data.scan_url;
      link.textContent = data.scan_url;
    } catch (_) {
      // The page will try again on the next refresh interval.
    }
  };
  setInterval(refreshQr, 4000);
}

document.querySelector('#copy-link')?.addEventListener('click', async event => {
  const link = document.querySelector('#scan-url').href;
  try {
    await navigator.clipboard.writeText(link);
    event.currentTarget.textContent = 'Copied ✓';
    setTimeout(() => event.currentTarget.textContent = 'Copy link', 1800);
  } catch {
    window.prompt('Copy this student scan link:', link);
  }
});
