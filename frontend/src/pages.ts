// The unified windu navigation tree. Each page maps to a Django REST endpoint
// that returns {title, sections:[...]} rendered generically by PageView.
// memberOnly groups (the IS-only tabs) are hidden unless /api/me says is_is_member.

export type Page = {
  id: string;
  label: string;
  endpoint: string;
  // When set, the shell embeds the original is-cmdb Django view at this path
  // (iframe, ?embed=1) instead of rendering the React "sections" API.
  embed?: string;
};

export type NavGroup = {
  label: string;
  memberOnly?: boolean;
  pages: Page[];
};

const p = (id: string, label: string, embed?: string): Page => ({
  id,
  label,
  endpoint: `/api/pages/${id}/`,
  ...(embed ? { embed } : {}),
});

export const NAV: NavGroup[] = [
  {
    label: "Operations",
    memberOnly: true,
    pages: [
      p("standup", "Stand up"), // React board
      p("pulse_history", "Pulse history"), // per-pulse·region counts (colored, region filter)
      p("isreq", "ISReq", "8501:/"), // jira-analysis Streamlit (north-star + ISReq pages)
      p("pagerduty", "PagerDuty", "8501:/PagerDuty_Overview"), // jira-analysis Streamlit
    ],
  },
  {
    // New tab — seeded with the ISDB board moved out of Operations; iterate later.
    label: "Roadmap",
    memberOnly: true,
    pages: [
      p("isdb", "ISDB"), // React (ISDB project tickets)
    ],
  },
  {
    label: "CMDB",
    pages: [
      p("cmdb_juju", "Juju models", "/"),
      p("cmdb_clouds", "Clouds", "/clouds/"),
      p("cmdb_charms", "Charms", "/charms/"),
      p("cmdb_cia", "CIA Assessment", "/cia/"),
      p("cmdb_teams", "Teams", "/teams/"),
      p("cmdb_ps6", "PS6 ManSol K8s", "/k8s/"),
    ],
  },
  {
    label: "IS Services",
    pages: [
      p("is_overview", "Overview", "/services/"),
      p("is_juju", "Juju", "/services/juju/"),
      p("is_vmaas", "VM aaS", "/"),
      p("is_dbaas", "DBaaS", "/dbaas/"),
      p("is_ck8saas", "Ck8s aaS", "/ck8s-aas/"),
      p("is_jenkinsaas", "Jenkins aaS", "/jenkins-aas/"),
      p("is_builders", "Builders", "/builders/"),
      p("is_storage", "Storage", "/storage/"),
      p("is_ccng", "CCNG"), // new — React placeholder
      p("is_ps7ingress", "PS7+ Ingress"), // new — React placeholder
    ],
  },
  {
    label: "GitOps",
    pages: [p("gitops_juju", "Juju models", "/gitops/"), p("gitops_dora", "DORA Metrics", "/dora/")],
  },
  {
    label: "Change management",
    pages: [
      p("change_maintenance", "Maintenance windows", "/maintenance/"),
      p("change_new", "New change requests", "/changes/new/"),
      p("change_cab", "CAB", "/changes/"),
      p("change_my_reviews", "My changes to review", "/changes/?me={email}"),
    ],
  },
];

/** Flatten to a lookup of all pages, regardless of group. */
export const ALL_PAGES: Page[] = NAV.flatMap((g) => g.pages);

// --- URL routing -----------------------------------------------------------
// Each page is reachable at /<pageId> (e.g. /standup, /gitops_dora) and each
// category at its slug (e.g. /gitops -> first subtab). Query params (?region=AMER)
// are read by the page components.
export const GROUP_SLUGS: Record<string, string> = {
  Operations: "operations",
  Roadmap: "roadmap",
  CMDB: "cmdb",
  "IS Services": "is-services",
  GitOps: "gitops",
  "Change management": "change-management",
};

export function groupSlug(label: string): string {
  return GROUP_SLUGS[label] ?? label.toLowerCase().replace(/\s+/g, "-");
}

/** Resolve the first URL path segment to a {group, page}, or null if unknown. */
export function resolvePath(seg: string): { group: NavGroup; page: Page } | null {
  if (!seg) return null;
  const s = seg.toLowerCase();
  for (const g of NAV) {
    if (groupSlug(g.label) === s) return { group: g, page: g.pages[0] };
  }
  for (const g of NAV) {
    const p = g.pages.find((pg) => pg.id === s);
    if (p) return { group: g, page: p };
  }
  return null;
}
