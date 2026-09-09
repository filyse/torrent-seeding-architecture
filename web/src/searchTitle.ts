/** Разбор scene-имени раздачи: сериал + сезон, без качеств. */

const SEASON_TOKEN = /^(?:s|season)(\d{1,2})(?:e\d{1,3})?$/i;
const QUALITY_TOKEN =
  /^(?:hd)?(?:2160|1080|720|576|480)p$|^(?:4k|uhd|webrip|web-dl|webdl|bluray|bdrip|hdtv|xvid|x264|x265|hevc|remux)$/i;

export type ReleaseTitle = {
  title: string;
  pretty: string;
  season: number | null;
  query: string;
};

export type TitleGroup = ReleaseTitle & {
  count: number;
  label: string;
};

export function prettyTitle(title: string): string {
  return title.replace(/[._]+/g, " ").replace(/\s+/g, " ").trim();
}

export function formatSeason(season: number | null): string {
  return season == null ? "" : `${season} сезон`;
}

export function formatReleaseCount(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return `${n} раздача`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${n} раздачи`;
  return `${n} раздач`;
}

export function parseReleaseName(name: string): ReleaseTitle {
  const parts = name.split(/[.\s_]+/).filter(Boolean);
  const titleParts: string[] = [];
  let season: number | null = null;
  for (const part of parts) {
    const seasonMatch = part.match(SEASON_TOKEN);
    if (seasonMatch) {
      season = Number(seasonMatch[1]);
      break;
    }
    if (QUALITY_TOKEN.test(part)) break;
    titleParts.push(part);
  }
  const title = titleParts.join(".") || name.trim();
  const query = season != null ? `${title}.s${String(season).padStart(2, "0")}` : title;
  return { title, pretty: prettyTitle(title), season, query };
}

export function groupReleasesBySeason(
  items: { display_name: string; label?: string }[],
): TitleGroup[] {
  const groups = new Map<string, TitleGroup>();
  for (const item of items) {
    const parsed = parseReleaseName(item.display_name);
    const key = `${parsed.title.toLowerCase()}|${parsed.season ?? ""}`;
    const found = groups.get(key);
    if (!found) {
      groups.set(key, {
        ...parsed,
        count: 1,
        label: (item.label || "").trim(),
      });
      continue;
    }
    found.count += 1;
    const label = (item.label || "").trim();
    if (found.label && label && found.label !== label) found.label = "";
  }
  return [...groups.values()].sort((a, b) => {
    const byName = a.pretty.localeCompare(b.pretty, "ru");
    if (byName !== 0) return byName;
    return (a.season ?? 999) - (b.season ?? 999);
  });
}
