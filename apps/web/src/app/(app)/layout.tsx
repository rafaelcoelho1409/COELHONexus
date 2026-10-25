import { AppShell } from "@/shared/layout/app-shell";

/** Wraps operator-console routes in the shared app chrome. */
export default function Layout({ children }: { children: React.ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
