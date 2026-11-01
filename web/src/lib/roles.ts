export type Role = "admin" | "security";

/** Single source of truth for who may open which area. Used by middleware, nav and API guards. */
export const ROUTE_ROLES: { prefix: string; roles: Role[] }[] = [
  { prefix: "/sources", roles: ["admin"] },
  { prefix: "/users", roles: ["admin"] },
];

export const can = (role: Role, prefix: string) => {
  const rule = ROUTE_ROLES.find((r) => prefix === r.prefix || prefix.startsWith(r.prefix + "/"));
  return !rule || rule.roles.includes(role);
};
