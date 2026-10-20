// The top bar's pop-up menus (account, settings, log in): a button that opens
// a panel under it. One is open at a time; Escape or a click elsewhere closes it.

const menus = []; // { button, panel, onOpen }

function setOpen(menu, open) {
  menu.panel.classList.toggle("hidden", !open);
  menu.button.setAttribute("aria-expanded", String(open));
  if (open) menu.onOpen();
}

export function closeMenus() {
  for (const menu of menus) setOpen(menu, false);
}

// `onOpen` runs each time the panel opens (to focus a field, clear a message).
export function registerMenu(buttonId, panelId, onOpen = () => {}) {
  const menu = { button: document.getElementById(buttonId), panel: document.getElementById(panelId), onOpen };
  menus.push(menu);
  menu.button.addEventListener("click", () => {
    const open = menu.panel.classList.contains("hidden");
    closeMenus();
    setOpen(menu, open);
  });
}

export function openMenu(panelId) {
  closeMenus();
  setOpen(
    menus.find((menu) => menu.panel.id === panelId),
    true,
  );
}

document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) closeMenus();
});
document.addEventListener("keydown", (e) => {
  const open = menus.find((menu) => !menu.panel.classList.contains("hidden"));
  if (e.key === "Escape" && open) {
    setOpen(open, false);
    open.button.focus();
  }
});
