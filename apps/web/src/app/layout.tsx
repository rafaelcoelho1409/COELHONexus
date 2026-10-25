import type { Metadata, Viewport } from "next";
import { Archivo, JetBrains_Mono } from "next/font/google";

import { themeInitScript } from "@/shared/lib/theme";
import { QueryProvider } from "@/shared/providers/query-provider";

import "@/shared/styles/globals.css";

const archivo = Archivo({
  variable: "--font-archivo",
  subsets: ["latin"],
  display: "swap",
});

const jetbrains = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "COELHO Nexus — Turn framework docs into study guides. Ask any channel.",
  description:
    "Docs Distiller turns a framework's documentation into a chapter-by-chapter study guide. YouTube Content Search indexes a channel and answers questions with citations.",
};

export const viewport: Viewport = {
  colorScheme: "dark light",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0c10" },
    { media: "(prefers-color-scheme: light)", color: "#fcfcfd" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${archivo.variable} ${jetbrains.variable}`} suppressHydrationWarning>
      <head>
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: a constant theme bootstrap string — no user input — that must run before first paint */}
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <a href="#main" className="skip-link">
          Skip to content
        </a>
        <QueryProvider>{children}</QueryProvider>
      </body>
    </html>
  );
}
