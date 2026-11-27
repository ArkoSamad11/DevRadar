"use strict";
// Runs before the page draws so a saved light or dark choice never flashes the other theme.
try {
  const saved = localStorage.getItem("devradar-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
} catch { /* Storage can be blocked; the system theme is used instead. */ }
