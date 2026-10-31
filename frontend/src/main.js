// Entry point: wires the feature modules together, then waits for a login.
//
//   api.js        every backend call          state.js    shared app state
//   lib.js        DOM helpers, fetch + cookie  storage.js  per-browser preferences
//   ui/           icons, small widgets
//   features/     one module per part of the screen (see each file's first comment)
import "./style.css";
import { $ } from "./lib.js";
import { logout, requireLogin } from "./features/auth.js";
import { initChats, loadChats, renderAll, renderList } from "./features/chats.js";
import { initComposer, updateSend } from "./features/composer.js";
import { initDocuments, refreshDocs } from "./features/documents.js";
import { initEvals } from "./features/evals.js";
import { initMessages } from "./features/messages.js";
import { closeSidebarOnMobile, initSidebar } from "./features/sidebar.js";
import { initTheme } from "./features/theme.js";

initTheme();
initSidebar();
const evalsView = initEvals({ button: $("evals-btn"), view: $("evals"), app: $("app"), onToggle: () => renderList() });
$("evals-btn").addEventListener("click", closeSidebarOnMobile);
initChats({ evalsView });
initMessages();
initComposer();
initDocuments();

renderAll();
updateSend();
$("logout").addEventListener("click", logout);
requireLogin().then((user) => {
  $("whoami").textContent = user.username;
  $("whoami").title = "Logged in as " + user.username;
  refreshDocs();
  loadChats();
});
