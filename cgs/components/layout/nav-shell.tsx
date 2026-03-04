"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { PropsWithChildren } from "react";

const NAV_ITEMS = [
  { href: "/", label: "Overview" },
  { href: "/documents", label: "Document Center" },
  { href: "/retrieval", label: "Retrieval" },
  { href: "/access", label: "Tenant Access" },
  { href: "/bindings", label: "Discord Bindings" },
  { href: "/settings", label: "Settings" },
  { href: "/secrets", label: "Secrets" },
  { href: "/changes", label: "Change Queue" },
  { href: "/audit", label: "Audit" }
];

export function NavShell({ children }: PropsWithChildren): JSX.Element {
  const pathname = usePathname();
  const normalized = pathname?.replace(/^\/cgs/, "") || "/";

  return (
    <div className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Catalyst Group Solutions</p>
          <h1>CGS Operator Console</h1>
        </div>
      </header>
      <nav className="main-nav" aria-label="Primary">
        {NAV_ITEMS.map((item) => {
          const active = normalized === item.href;
          return (
            <Link key={item.href} href={item.href} className={active ? "active" : ""}>
              {item.label}
            </Link>
          );
        })}
      </nav>
      <main className="content-grid">{children}</main>
    </div>
  );
}
