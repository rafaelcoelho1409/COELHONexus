import { BD_SECTION } from "@/features/bd";
import { SETTINGS_SECTION } from "@/features/settings";
import { YCS_SECTION } from "@/features/ycs";
import { AppShell } from "@/shared/layout/app-shell";

/** In the order of the primary nav; the second bar shows the section the path is in. */
const SECTIONS = [YCS_SECTION, BD_SECTION, SETTINGS_SECTION];

/** Wraps operator-console routes in the shared app chrome. */
export default function Layout({ children }: { children: React.ReactNode }) {
  return <AppShell sections={SECTIONS}>{children}</AppShell>;
}
