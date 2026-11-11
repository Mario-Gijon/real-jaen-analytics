import { MISSING } from "@/lib/format";

// Use the runtime's country names in both languages; unknown source labels stay intact.
const english = new Intl.DisplayNames(["en"], { type: "region", fallback: "none" });
const spanish = new Intl.DisplayNames(["es"], { type: "region", fallback: "none" });
const countries = new Map<string, string>();
for (let first = 65; first <= 90; first++) {
  for (let second = 65; second <= 90; second++) {
    const code = String.fromCharCode(first, second);
    const name = english.of(code);
    const translated = spanish.of(code);
    if (name && translated) {
      countries.set(code.toLowerCase(), translated);
      countries.set(name.toLowerCase(), translated);
    }
  }
}

/** Presentation only: preserve the API's value and any label we cannot translate reliably. */
export function countryLabel(country: string | null): string {
  return country === null ? MISSING : countries.get(country.trim().toLowerCase()) ?? country;
}
