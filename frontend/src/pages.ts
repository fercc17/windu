// The unified windu navigation tree. Each page maps to a Django REST endpoint
// that returns {title, sections:[...]} rendered generically by PageView.
// memberOnly groups (the IS-only tabs) are hidden unless /api/me says is_is_member.

export type Page = {
  id: string;
  label: string;
  endpoint: string;
};

export type NavGroup = {
  label: string;
  memberOnly?: boolean;
  pages: Page[];
};

const p = (id: string, label: string): Page => ({
  id,
  label,
  endpoint: `/api/pages/${id}/`,
});

export const NAV: NavGroup[] = [
  {
    label: "Operations",
    memberOnly: true,
    pages: [
      p("standup", "Stand up"),
      p("isreq", "ISReq"),
      p("isdb", "ISDB"),
      p("pagerduty", "PagerDuty"),
    ],
  },
  {
    label: "CMDB",
    pages: [
      p("cmdb_juju", "Juju models"),
      p("cmdb_clouds", "Clouds"),
      p("cmdb_charms", "Charms"),
      p("cmdb_cia", "CIA Assessment"),
      p("cmdb_teams", "Teams"),
      p("cmdb_ps6", "PS6 ManSol K8s"),
    ],
  },
  {
    label: "IS Services",
    pages: [
      p("is_overview", "Overview"),
      p("is_juju", "Juju"),
      p("is_vmaas", "VM aaS"),
      p("is_dbaas", "DBaaS"),
      p("is_ck8saas", "Ck8s aaS"),
      p("is_jenkinsaas", "Jenkins aaS"),
      p("is_builders", "Builders"),
      p("is_storage", "Storage"),
      p("is_ccng", "CCNG"),
      p("is_ps7ingress", "PS7+ Ingress"),
    ],
  },
  {
    label: "GitOps",
    pages: [p("gitops_juju", "Juju models"), p("gitops_dora", "DORA Metrics")],
  },
  {
    label: "Change management",
    pages: [
      p("change_maintenance", "Maintenance windows"),
      p("change_new", "New change requests"),
      p("change_cab", "CAB"),
    ],
  },
];

/** Flatten to a lookup of all pages, regardless of group. */
export const ALL_PAGES: Page[] = NAV.flatMap((g) => g.pages);
