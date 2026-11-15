// Profile photos. The server only gives one with the session token, which an
// <img src> can't send, so the photo is fetched with api() and shown from a
// blob: URL. Each photo's URL changes when it changes (?v=), so it is fetched
// once per page load and the browser's cache does the rest.
// Used by the top bar's badge and Settings (views/settings.js) and the admin's
// accounts table (views/admin.js).

import { api } from "./api.js";

const loaded = new Map(); // photo URL -> promise of a blob: URL

const PLACEHOLDER =
  '<svg viewBox="0 0 24 24"><circle cx="12" cy="9.5" r="4.5"></circle><path d="M2.5 25c0-5.5 4.3-9.5 9.5-9.5s9.5 4 9.5 9.5z"></path></svg>';

function blobUrl(url) {
  if (!loaded.has(url)) {
    loaded.set(
      url,
      api(url, { as: "blob" }).then(
        (blob) => URL.createObjectURL(blob),
        () => null,
      ),
    );
  }
  return loaded.get(url);
}

// Fills an .avatar element: the photo when there is one, the person icon otherwise.
export async function showAvatar(el, url) {
  el.dataset.photo = url || "";
  const src = url ? await blobUrl(url) : null;
  if (el.dataset.photo !== (url || "")) return; // a newer photo was asked for meanwhile
  if (!src) {
    el.innerHTML = PLACEHOLDER;
    return;
  }
  const img = document.createElement("img");
  img.src = src;
  img.alt = "";
  el.replaceChildren(img);
}
