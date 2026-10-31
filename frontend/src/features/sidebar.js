// The sidebar: open/closed (remembered on desktop), and closed again after a pick on mobile
import { $ } from "../lib.js";
import { load, store } from "../storage.js";

const mobile = matchMedia("(max-width: 800px)");
const layout = () => $("layout");

export const closeSidebarOnMobile = () => mobile.matches && layout().classList.remove("sidebar-open");

function setSidebar(open) {
  layout().classList.toggle("sidebar-open", open);
  if (!mobile.matches) store("sidebarOpen", open);
}

// e.g. to show upload progress, without changing the saved preference
export function revealSidebar() {
  if (!mobile.matches) layout().classList.add("sidebar-open");
}

export function initSidebar() {
  layout().classList.toggle("sidebar-open", !mobile.matches && load("sidebarOpen", true));
  $("menu").addEventListener("click", () => setSidebar(true));
  $("collapse").addEventListener("click", () => setSidebar(false));
  $("backdrop").addEventListener("click", () => layout().classList.remove("sidebar-open"));
}
