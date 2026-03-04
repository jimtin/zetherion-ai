import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import AccessPage from "@/app/access/page";
import AuditPage from "@/app/audit/page";
import ChangesPage from "@/app/changes/page";
import DocumentsPage from "@/app/documents/page";
import OverviewPage from "@/app/page";
import RetrievalPage from "@/app/retrieval/page";
import SecretsPage from "@/app/secrets/page";
import SettingsPage from "@/app/settings/page";
import BindingsPage from "@/app/bindings/page";

describe("route-level page rendering", () => {
  it("renders overview", () => {
    render(<OverviewPage />);
    expect(screen.getByText(/CGS \/service\/ai\/v1 Operator Interface/i)).toBeInTheDocument();
  });

  it("renders documents", () => {
    render(<DocumentsPage />);
    expect(screen.getByRole("heading", { name: /Document Center/i })).toBeInTheDocument();
  });

  it("renders retrieval", () => {
    render(<RetrievalPage />);
    expect(screen.getByRole("heading", { name: /Retrieval Panel/i })).toBeInTheDocument();
  });

  it("renders access", () => {
    render(<AccessPage />);
    expect(screen.getByRole("heading", { name: /Discord Users \+ Roles/i })).toBeInTheDocument();
  });

  it("renders bindings", () => {
    render(<BindingsPage />);
    expect(screen.getByRole("heading", { name: /Guild \+ Channel Bindings/i })).toBeInTheDocument();
  });

  it("renders settings", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /Allowlisted Key Editor/i })).toBeInTheDocument();
  });

  it("renders secrets", () => {
    render(<SecretsPage />);
    expect(screen.getByRole("heading", { name: /Metadata \+ Rotate\/Delete/i })).toBeInTheDocument();
  });

  it("renders changes", () => {
    render(<ChangesPage />);
    expect(screen.getByRole("heading", { name: /Change Queue/i })).toBeInTheDocument();
  });

  it("renders audit", () => {
    render(<AuditPage />);
    expect(screen.getByRole("heading", { name: /Audit Timeline/i })).toBeInTheDocument();
  });
});
