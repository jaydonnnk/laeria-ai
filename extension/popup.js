// Sign-in and connection settings. Credentials go straight to the service
// worker and are never held here.

import { getConfig, setConfig } from "./config.js";

const $ = (id) => document.getElementById(id);
const send = (msg) => new Promise((r) => chrome.runtime.sendMessage(msg, r));

function say(text, kind = "") {
  $("msg").textContent = text;
  $("msg").className = `msg ${kind}`;
}

async function paintAuth() {
  const res = await send({ type: "AUTH_STATUS" });
  const status = res?.data ?? { signedIn: false, configured: false };

  $("signed-in").classList.toggle("hidden", !status.signedIn);
  $("signed-out").classList.toggle("hidden", status.signedIn);
  $("who").textContent = status.email || "";

  if (!status.configured) {
    // Nothing works until the three public values are present, so open the
    // panel rather than letting sign-in fail with a confusing auth error.
    $("settings").open = true;
    say("Add the connection settings below to get started.", "err");
  }
}

async function paintConfig() {
  const cfg = await getConfig();
  $("apiUrl").value = cfg.apiUrl || "";
  $("supabaseUrl").value = cfg.supabaseUrl || "";
  $("supabaseAnonKey").value = cfg.supabaseAnonKey || "";
}

$("signin").addEventListener("click", async () => {
  const email = $("email").value.trim();
  const password = $("password").value;
  if (!email || !password) return say("Email and password are required.", "err");

  $("signin").disabled = true;
  say("Signing in…");
  const res = await send({ type: "SIGN_IN", email, password });
  $("signin").disabled = false;

  if (!res?.ok) return say(res?.error || "sign-in failed", "err");
  $("password").value = "";
  say("Signed in.", "ok");
  paintAuth();
});

$("signout").addEventListener("click", async () => {
  await send({ type: "SIGN_OUT" });
  say("Signed out.");
  paintAuth();
});

$("save").addEventListener("click", async () => {
  await setConfig({
    apiUrl: $("apiUrl").value.trim().replace(/\/+$/, ""),
    supabaseUrl: $("supabaseUrl").value.trim().replace(/\/+$/, ""),
    supabaseAnonKey: $("supabaseAnonKey").value.trim(),
  });
  say("Saved.", "ok");
  paintAuth();
});

$("password").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("signin").click();
});

paintConfig().then(paintAuth);
