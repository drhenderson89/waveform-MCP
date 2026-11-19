// Preview UI client.
//
// Connects to /ws, reads events as they arrive, and maintains a local model
// of the current Edit. Renders a track list with clips and a tiny inline
// piano-roll for MIDI clips. Activity log on the right.
//
// The model is reconstructed from event diffs, not pulled in one shot. The
// initial flood of buffered events from the server is enough to bootstrap.

const $ = sel => document.querySelector(sel);
const tracksEl = $("#tracks");
const eventsEl = $("#events");
const statusEl = $("#status");
const metaEl = $("#meta");

// ---- model ----
const model = {
  edit: { name: "—", tempo: 120, length_beats: 64, time_sig: [4, 4], tracks: [] },
};

function findTrack(id) {
  return model.edit.tracks.find(t => t.id === id);
}

function applyEvent(evt) {
  if (evt.kind === "edit_opened" && evt.summary) {
    Object.assign(model.edit, {
      name: evt.summary.name,
      tempo: evt.summary.tempo,
      time_sig: evt.summary.time_signature,
      length_beats: evt.summary.length_beats,
      tracks: evt.summary.tracks.map(t => ({
        id: t.id, name: t.name, kind: t.kind,
        volume_db: t.volume_db, pan: t.pan,
        clips: (t.clips || []).map(c => ({ ...c, notes: [] })),
      })),
    });
    return;
  }

  if (evt.kind !== "model_change") return;
  const diff = evt.diff || {};

  if (diff.edit) {
    for (const [k, v] of Object.entries(diff.edit)) {
      const target = k === "time_sig_num" ? "time_sig" : k;
      model.edit[target] = v.to ?? v;
    }
  }

  if (diff.tracks_added) {
    for (const t of diff.tracks_added) {
      model.edit.tracks.push({
        id: t.id, name: t.name, kind: t.kind,
        volume_db: t.volume_db ?? 0, pan: t.pan ?? 0,
        clips: (t.clips || []).map(c => ({ ...c, notes: c.notes || [] })),
      });
    }
  }
  if (diff.tracks_removed) {
    model.edit.tracks = model.edit.tracks.filter(t => !diff.tracks_removed.includes(t.id));
  }

  if (diff.tracks_changed) {
    for (const ch of diff.tracks_changed) {
      const t = findTrack(ch.track_id);
      if (!t) continue;
      for (const f of ["name", "volume_db", "pan", "mute", "solo", "output"]) {
        if (ch[f]) t[f] = ch[f].to;
      }
      if (ch.clips_added) {
        for (const c of ch.clips_added) t.clips.push({ ...c, notes: c.notes || [] });
      }
      if (ch.clips_removed) {
        t.clips = t.clips.filter(c => !ch.clips_removed.includes(c.id));
      }
      if (ch.notes_changed) {
        for (const n of ch.notes_changed) {
          const c = t.clips.find(x => x.id === n.clip_id);
          if (c) c.note_count = n.to;
        }
      }
      if (ch.plugins) t.plugins = ch.plugins;
    }
  }
}

// ---- render ----

function pixelsPerBeat() {
  const lane = document.querySelector(".lane");
  const w = lane ? lane.clientWidth : tracksEl.clientWidth;
  return Math.max(1, w / Math.max(model.edit.length_beats, 16));
}

function renderTracks() {
  metaEl.textContent =
    `${model.edit.name}  ·  ${model.edit.tempo} BPM  ·  ` +
    `${model.edit.time_sig?.[0] ?? 4}/${model.edit.time_sig?.[1] ?? 4}  ·  ` +
    `${model.edit.length_beats} beats  ·  ${model.edit.tracks.length} tracks`;

  const ppb = pixelsPerBeat();
  const totalLen = Math.max(model.edit.length_beats, 16);

  tracksEl.innerHTML = "";
  for (const t of model.edit.tracks) {
    const trackEl = document.createElement("div");
    trackEl.className = "track";
    trackEl.innerHTML = `
      <div class="track-head">
        <span class="track-name">${escapeHtml(t.name)}</span>
        <span class="track-meta">${escapeHtml(t.kind)} · ${Number(t.volume_db).toFixed(1)} dB · pan ${Number(t.pan).toFixed(2)}</span>
      </div>
      <div class="lane"></div>
    `;
    const lane = trackEl.querySelector(".lane");
    for (const c of t.clips || []) {
      const clip = document.createElement("div");
      clip.className = "clip";
      const left = (c.start / totalLen) * 100;
      const width = Math.max(0.5, (c.length / totalLen) * 100);
      clip.style.left = `${left}%`;
      clip.style.width = `${width}%`;
      clip.title = `${c.name} · ${c.kind} · ${c.start}→${c.start + c.length} · ${c.note_count ?? 0} notes`;
      clip.textContent = c.name;
      // tiny piano-roll: notes as horizontal lines inside the clip
      if (c.kind === "midi" && c.notes && c.notes.length) {
        const minP = Math.min(...c.notes.map(n => n.pitch));
        const maxP = Math.max(...c.notes.map(n => n.pitch));
        const span = Math.max(1, maxP - minP);
        for (const n of c.notes) {
          const noteEl = document.createElement("div");
          noteEl.className = "note";
          noteEl.style.left = `${((n.start_beats - 0) / c.length) * 100}%`;
          noteEl.style.width = `${(n.length_beats / c.length) * 100}%`;
          noteEl.style.bottom = `${((n.pitch - minP) / span) * 100}%`;
          clip.appendChild(noteEl);
        }
      }
      lane.appendChild(clip);
    }
    tracksEl.appendChild(trackEl);
  }
}

function renderEvent(evt) {
  const li = document.createElement("li");
  const time = (evt.ts || "").slice(11, 19);
  let cls = "", body = "";
  if (evt.kind === "tool_call") {
    cls = evt.result === "error" ? "evt-error" : "evt-tool";
    body = `<strong>${escapeHtml(evt.tool)}</strong>` + (evt.intent ? ` <span class="evt-intent">— ${escapeHtml(evt.intent)}</span>` : "");
    if (evt.error) body += ` <span class="evt-error">[${escapeHtml(evt.error)}]</span>`;
  } else if (evt.kind === "narrate") {
    cls = "evt-narrate"; body = escapeHtml(evt.text || "");
  } else if (evt.kind === "flush") {
    cls = "evt-flush"; body = `flush → ${escapeHtml(evt.target || "")}`;
  } else if (evt.kind === "edit_opened") {
    cls = "evt-flush"; body = `opened ${escapeHtml(evt.path || "")}`;
  } else {
    body = `<em>${escapeHtml(evt.kind)}</em>`;
  }
  li.className = cls;
  li.innerHTML = `<span class="evt-time">${escapeHtml(time)}</span>${body}`;
  eventsEl.prepend(li);
  // cap to 200
  while (eventsEl.children.length > 200) eventsEl.lastChild.remove();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]
  ));
}

// ---- websocket ----

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);

  ws.onopen = () => {
    statusEl.textContent = "live";
    statusEl.classList.add("live");
    statusEl.classList.remove("dead");
  };
  ws.onclose = () => {
    statusEl.textContent = "disconnected";
    statusEl.classList.add("dead");
    statusEl.classList.remove("live");
    setTimeout(connect, 1500);
  };
  ws.onmessage = ev => {
    try {
      const evt = JSON.parse(ev.data);
      applyEvent(evt);
      renderEvent(evt);
      renderTracks();
    } catch {
      // ignore malformed
    }
  };
}

connect();
window.addEventListener("resize", renderTracks);
renderTracks();
