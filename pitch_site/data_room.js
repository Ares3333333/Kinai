const setText = (id, value) => {
  const node = document.getElementById(id);
  if (node) node.textContent = value;
};

const fmt = (value) => new Intl.NumberFormat("en-US").format(Number(value) || 0);
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#039;",
}[char]));

function compactExample(row) {
  if (!row) return "No data";
  const json = JSON.stringify(row);
  return json.length > 420 ? `${json.slice(0, 420)}...` : json;
}

function renderFiles(files) {
  const root = document.getElementById("dataRoomFiles");
  if (!root) return;
  root.innerHTML = files.map((file) => `
    <article class="data-room-file">
      <div>
        <span>${escapeHTML(file.schema)}</span>
        <h2>${escapeHTML(file.file)}</h2>
        <p>${escapeHTML(file.description)}</p>
      </div>
      <dl>
        <dt>Rows</dt><dd>${fmt(file.rows)}</dd>
        <dt>Bytes</dt><dd>${fmt(file.bytes)}</dd>
        <dt>Labels</dt><dd>${escapeHTML(file.labels)}</dd>
        <dt>Raw video</dt><dd>${file.contains_raw_video ? "yes" : "no"}</dd>
        <dt>Raw audio</dt><dd>${file.contains_raw_audio ? "yes" : "no"}</dd>
      </dl>
      <pre>${escapeHTML(compactExample(file.example_row))}</pre>
    </article>
  `).join("");
}

async function loadDataRoom() {
  const room = await fetch("/api/data-room", { cache: "no-store" }).then((r) => r.json());
  setText("rawVideo", room.raw_video_saved ? "stored" : "no");
  setText("rawAudio", room.raw_audio_saved ? "stored" : "no");
  setText("roomLabels", fmt(room.validation?.total_alerts_labelled));
  setText("roomFiles", fmt((room.files || []).length));
  renderFiles(room.files || []);
}

loadDataRoom().catch(() => {
  setText("roomFiles", "API unavailable");
});
