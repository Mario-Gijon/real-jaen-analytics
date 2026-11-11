import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

/*
 * Architectural boundary (see README): UI → src/client → Next.js BFF (/api/v1) → DataSource → adapter.
 * Pages, features, UI and the client never import server code or data files;
 * only the API route handlers (src/app/api) and the access gate (src/proxy.ts),
 * which runs on the server before every request, reach src/server.
 */
const serverAndData = {
  group: ["@/server", "@/server/*", "**/server/*", "**/data/*", "*.json"],
  message:
    "La interfaz no accede a src/server ni a data/: usa el cliente tipado (src/client) contra /api/v1.",
};

/** The server side never depends on screens or on the browser client. */
const uiFromServer = {
  group: ["@/ui", "@/ui/*", "@/features/*", "@/client", "@/client/*", "@/app/*"],
  message: "src/server sólo implementa el contrato; no importa interfaz ni cliente.",
};

/** Shared library modules stay below features, UI, the client, and the server. */
const appFromLib = {
  group: [
    "@/app", "@/app/*", "**/app/**",
    "@/features", "@/features/*", "**/features/**",
    "@/ui", "@/ui/*", "**/ui/**",
    "@/client", "@/client/*", "**/client/**",
    "@/server", "@/server/*", "**/server/**",
  ],
  message: "src/lib contiene utilidades compartidas y no depende de capas de aplicación.",
};

/** Shared hooks may call the typed client, but do not depend on a screen or feature. */
const appFromHook = {
  group: [
    "@/app", "@/app/*", "**/app/**",
    "@/features", "@/features/*", "**/features/**",
    "@/ui", "@/ui/*", "**/ui/**",
  ],
  message: "src/hooks puede usar el cliente tipado, pero no depende de pantallas ni features.",
};

const eslintConfig = [
  { ignores: [".next/**", "next-env.d.ts", "playwright-report/**", "test-results/**"] },
  ...nextVitals,
  ...nextTs,
  {
    files: ["src/**/*.{ts,tsx}", "tests/**/*.{ts,tsx}"],
    ignores: ["src/app/api/**", "src/proxy.ts", "src/server/**", "tests/api/**", "tests/data/**"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [serverAndData] }],
    },
  },
  {
    files: ["src/server/**/*.{ts,tsx}", "tests/data/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [uiFromServer] }],
    },
  },
  {
    files: ["src/lib/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [appFromLib] }],
    },
  },
  {
    files: ["src/hooks/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": ["error", { patterns: [appFromHook] }],
    },
  },
];

export default eslintConfig;
