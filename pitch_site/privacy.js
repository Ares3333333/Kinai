function li(text) {
  return `<li>${text}</li>`;
}

function fileCard(file) {
  const kb = Math.round((file.bytes || 0) / 1024);
  return `<article class="content-card"><strong>${file.file}</strong><p>${file.exists ? `${kb} KB` : "нет файла"}<br>Raw video: ${file.contains_raw_video ? "да" : "нет"}<br>Derived signals: ${file.contains_sensitive_derived_signals ? "да" : "нет"}</p></article>`;
}

async function loadPrivacy() {
  const passport = await fetch("/api/privacy-passport", { cache: "no-store" }).then((r) => r.json());
  document.getElementById("storedList").innerHTML = passport.what_is_stored.map(li).join("");
  document.getElementById("notStoredList").innerHTML = passport.what_is_not_stored_by_default.map(li).join("");
  document.getElementById("dataFiles").innerHTML = passport.data_files.map(fileCard).join("");
}

loadPrivacy().catch(() => {
  document.getElementById("storedList").innerHTML = li("Privacy API недоступен.");
});
