/**
 * Chain Tester state — module-level singletons.
 *
 * The chain tester (#78) lives on a single SPA route
 * (``fallback-sources.vue``). Vue Router unmounts the page on
 * navigation away (e.g. operator clicks Cache or Users in the
 * sidebar) and re-mounts on return — which means component-local
 * ``ref()``s are recreated empty every time. From the operator's
 * point of view that's a state-loss bug: pending draft edits, a
 * half-typed simulate identity, or a per-URL token override evaporates
 * the moment they switch tabs to glance at something else.
 *
 * Hoisting the state to module scope makes it survive the
 * unmount/mount cycle — the module is only evaluated once per
 * page-load (i.e. once per "admin session" in dev / production where
 * the SPA bundle stays loaded). Browser refresh re-evaluates the
 * bundle from scratch and resets everything, which is the correct
 * trade-off: refresh = "I want a clean slate", route switch = "I'm
 * coming back, hold my place".
 *
 * On logout the admin store calls ``resetChainTesterState`` so the
 * next operator's draft doesn't inherit the previous one.
 */
import { ref } from "vue";

function _blankProbeForm() {
  return {
    op: "info",
    repo_type: "model",
    namespace: "",
    name: "",
    revision: "main",
    file_path: "",
    paths_csv: "",
  };
}

function _blankSimIdentity() {
  return { mode: "anonymous", username: "", user_id: null };
}

// ----- module-level singletons -----

// Tab the tester is parked on (Draft simulate vs Live real probe).
const probeTab = ref("simulate");

// Probe target (op, repo_type, namespace, name, revision, file_path,
// paths_csv) — shared across both tabs.
const probeForm = ref(_blankProbeForm());

// System state — Draft simulate tab.
const draftSources = ref([]);
const draftDirty = ref(false);

// User state — Draft simulate tab (impersonation + per-URL overrides).
const simIdentity = ref(_blankSimIdentity());
const simHeaderTokens = ref([]);

// User state — Live real probe tab (admin's own credentials).
const realKhubToken = ref("");
const realHeaderTokens = ref([]);

// Whether ``loadDraftFromSystem`` has already run for this admin
// session. Distinguishes "first mount, draft empty because we just
// loaded the page" (auto-seed is helpful) from "draft empty because
// the operator explicitly clicked Discard" (auto-seed would be
// surprising). Refresh + logout reset this back to false.
const autoLoadDone = ref(false);

/**
 * Vend the singleton refs to the page component. The component
 * mounts and re-mounts but the ``ref`` instances stay the same, so
 * value changes persist.
 */
export function useChainTesterState() {
  return {
    probeTab,
    probeForm,
    draftSources,
    draftDirty,
    simIdentity,
    simHeaderTokens,
    realKhubToken,
    realHeaderTokens,
    autoLoadDone,
  };
}

/**
 * Wipe every chain-tester ref back to its initial state. Called by
 * the admin store on logout so the next operator's session starts
 * with a clean slate. Direct mutation rather than reassigning the
 * refs themselves so existing component bindings keep working.
 */
export function resetChainTesterState() {
  probeTab.value = "simulate";
  probeForm.value = _blankProbeForm();
  draftSources.value = [];
  draftDirty.value = false;
  simIdentity.value = _blankSimIdentity();
  simHeaderTokens.value = [];
  realKhubToken.value = "";
  realHeaderTokens.value = [];
  autoLoadDone.value = false;
}

// Vite HMR self-preservation: when this composable file (or any file
// that imports it) is hot-edited in dev, the module is re-evaluated
// and the singleton refs above are recreated empty — wiping any
// pending draft / edit / token. Stash the current state in
// ``import.meta.hot.data`` before disposal and restore it after the
// new module loads. Production builds have no ``import.meta.hot`` so
// this is a no-op cost.
//
// HMR is dev-time UX only; a real browser refresh is still meant to
// reset state (see useChainTesterState header docstring), and a
// logout still calls ``resetChainTesterState`` explicitly.
// ``import.meta.hot.data`` may be ``undefined`` in some test runtimes
// (vitest/jsdom can populate ``import.meta.hot`` itself but leave
// ``.data`` unset until vite plugin wires it up). Guard with ``?.`` so
// the module load never throws when HMR happens to be partially
// stubbed.
if (import.meta.hot) {
  const saved = import.meta.hot.data?.chainTesterState;
  if (saved) {
    probeTab.value = saved.probeTab;
    probeForm.value = saved.probeForm;
    draftSources.value = saved.draftSources;
    draftDirty.value = saved.draftDirty;
    simIdentity.value = saved.simIdentity;
    simHeaderTokens.value = saved.simHeaderTokens;
    realKhubToken.value = saved.realKhubToken;
    realHeaderTokens.value = saved.realHeaderTokens;
    autoLoadDone.value = saved.autoLoadDone;
  }
  import.meta.hot.dispose?.((data) => {
    if (!data) return;
    data.chainTesterState = {
      probeTab: probeTab.value,
      probeForm: probeForm.value,
      draftSources: draftSources.value,
      draftDirty: draftDirty.value,
      simIdentity: simIdentity.value,
      simHeaderTokens: simHeaderTokens.value,
      realKhubToken: realKhubToken.value,
      realHeaderTokens: realHeaderTokens.value,
      autoLoadDone: autoLoadDone.value,
    };
  });
}
