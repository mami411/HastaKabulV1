import {
  Baby,
  Bone,
  Brain,
  Droplet,
  Ear,
  Eye,
  HeartPulse,
  Microscope,
  PersonStanding,
  Ribbon,
  ScanFace,
  Scissors,
  Smile,
  Stethoscope,
  Venus,
  Wind,
  type LucideIcon,
} from "lucide-react";

/**
 * Poliklinik ve hekim adları için görüntüleme yardımcıları. Poliklinik listesi API'den gelir; burada
 * sabit bir liste yoktur, yalnız addan ikon tahmini vardır (bilinmeyen poliklinik → steteskop).
 */

/** Türkçe büyük/küçük harf ve aksan farkını yok sayan karşılaştırma anahtarı ("Göz" → "goz"). */
export function fold(text: string): string {
  return text
    .toLocaleLowerCase("tr-TR")
    .replace(/ç/g, "c")
    .replace(/ğ/g, "g")
    .replace(/ı/g, "i")
    .replace(/ö/g, "o")
    .replace(/ş/g, "s")
    .replace(/ü/g, "u")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

/**
 * Ad sözcüklerinin başıyla eşleşen anahtarlar → ikon. Sıra önemlidir: özel olan önce
 * ("Beyin Cerrahisi" nörolojiyle değil beyinle, "Çocuk Cerrahisi" çocukla eşleşir).
 */
const ICON_RULES: [readonly string[], LucideIcon][] = [
  [["kardiyo", "kalp"], HeartPulse],
  [["goz", "oftalm"], Eye],
  [["kbb", "kulak", "burun", "bogaz", "odyo"], Ear],
  [["ortoped", "travmat", "kemik"], Bone],
  [["noro", "beyin", "psikiyat", "psikolo"], Brain],
  [["cocuk", "pediat", "neonat", "yenidogan"], Baby],
  [["kadin", "dogum", "jinekol", "obstetri"], Venus],
  [["dermato", "cildiye", "cilt", "deri"], ScanFace],
  [["urolo", "nefrolo", "bobrek"], Droplet],
  [["gogus", "akciger", "solunum"], Wind],
  [["onkolo", "hematolo"], Ribbon],
  [["fizik", "rehabilit"], PersonStanding],
  [["dis", "agiz", "ortodont"], Smile],
  [["enfeksiyon", "patolo", "biyokimya", "mikrobiyo"], Microscope],
  [["cerrahi"], Scissors],
  [["dahiliye", "ic"], Stethoscope],
];

/** Poliklinik adı/kodundan ikon; eşleşme yoksa steteskop. */
export function clinicIcon(name: string, code = ""): LucideIcon {
  const words = fold(`${name} ${code}`).split(/[^a-z0-9]+/).filter(Boolean);
  for (const [keys, icon] of ICON_RULES) {
    if (words.some((word) => keys.some((key) => word.startsWith(key)))) return icon;
  }
  return Stethoscope;
}

/** Ad başındaki akademik/uzmanlık unvan sözcükleri. */
const TITLE_WORDS = new Set(["prof.", "doç.", "doc.", "dr.", "uzm.", "op.", "öğr.", "ogr.", "üyesi", "uyesi", "yrd."]);

/** "Uzm. Dr. Elif Kaya" → { title: "Uzm. Dr.", name: "Elif Kaya" }; unvan yoksa title boş. */
export function splitDoctorName(displayName: string): { title: string; name: string } {
  const words = displayName.trim().split(/\s+/);
  let index = 0;
  while (index < words.length - 1 && TITLE_WORDS.has(words[index].toLocaleLowerCase("tr-TR"))) index += 1;
  return { title: words.slice(0, index).join(" "), name: words.slice(index).join(" ") };
}

/** Hekim baş harfleri (avatar): unvan atlanır, en çok iki harf. */
export function doctorInitials(displayName: string): string {
  const { name } = splitDoctorName(displayName);
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((word) => word.charAt(0).toLocaleUpperCase("tr-TR"))
    .join("");
}
