/** Light/dark theme toggle; preference kept in localStorage. */
const STORAGE_KEY = "hub-theme";

function storedTheme() {
  const value = localStorage.getItem(STORAGE_KEY);
  return value === "light" || value === "dark" ? value : null;
}

function applyTheme(theme) {
  if (theme) document.documentElement.dataset.theme = theme;
  else delete document.documentElement.dataset.theme;
}

applyTheme(storedTheme());

export function toggleTheme() {
  const stored = storedTheme();
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
  const current =
    stored ??
    (prefersDark || document.documentElement.dataset.theme === "dark"
      ? "dark"
      : "light");
  const next = current === "dark" ? "light" : "dark";
  localStorage.setItem(STORAGE_KEY, next);
  applyTheme(next);
}

document.querySelectorAll("[data-theme-toggle]").forEach((el) => {
  el.addEventListener("click", toggleTheme);
});
