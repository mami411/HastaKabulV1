/**
 * Kurum markası: tek yer.
 *
 * Uygulamanın kullanıcıya görünen kurum adı, uygulama ve asistan adı, slogan, çağrı merkezi ve renk paleti
 * buradan gelir. Başka bir klinik/hastane için yeniden markalama:
 *   1. Bu dosyadaki değerleri değiştirin (renkler `colors` içinde; CSS değişkeni adları solda yazılı).
 *   2. `public/brand/logo.svg` dosyasını kurumun işaretiyle değiştirin; PNG ikonları README'deki komutla
 *      yeniden üretin (`public/icon-192.png`, `icon-512.png`, `icon-maskable-512.png`, `apple-touch-icon.png`).
 * Tarayıcı başlığı, tema rengi, PWA manifest'i (ad, kısa ad, renkler) ve CSS renk değişkenleri derlemede
 * `vite.config.ts` içindeki `kurumMarkasi` eklentisiyle bu dosyadan üretilir; bileşenler `BRAND`'i kullanır.
 *
 * Bu dosya hem tarayıcı kodunda hem Vite yapılandırmasında (Node) içe aktarılır: yalnız düz veri ve saf
 * fonksiyon içermeli (DOM yok).
 *
 * MedTech kurgusal bir özel hastanedir; ad, işaret, renkler ve çağrı merkezi numarası demo içindir.
 */

export const BRAND = {
  /** Kurumun tam adı (ör. giriş ekranı, ana sayfa karşılaması). */
  fullName: "MedTech Özel Hastanesi",
  /** Kısa ad (başlık çubuğu, metin içi). */
  shortName: "MedTech",
  /** Uygulamanın adı (tarayıcı başlığı, ana ekran simgesi altı). */
  appName: "MedTech Cep",
  /** Sesli/yazılı asistanın adı. */
  assistantName: "MedTech Asistan",
  /** Kısa slogan. */
  slogan: "Sağlığınız elinizin altında",
  /** Tarayıcı/manifest açıklaması. */
  description:
    "MedTech Özel Hastanesi cep uygulaması: sesle ya da yazarak randevu alın, ilaçlarınızı ve son muayenenizi sorun.",
  /** Çağrı merkezi. `tel` arama bağlantısında kullanılır; `note` doluysa numaranın yanında gösterilir. */
  callCenter: {
    display: "0850 000 00 00",
    tel: "+908500000000",
    note: "Demo numarası, gerçek bir hat değildir.",
  },
  /** İşaret dosyası (public/ altında); favicon ve uygulama içi logo aynı dosyadır. */
  logoUrl: "/brand/logo.svg",
  /**
   * Renk paleti → CSS değişkenleri (`--<ad>`). Metin renkleri (ink*) `surface` ve `paper` üzerinde,
   * beyaz yazı `brand` ve `brand-strong` üzerinde en az 4.5:1 karşıtlık verecek biçimde seçildi.
   */
  colors: {
    /** Ana renk: derin hastane mavisi (düğmeler, seçili öğeler, bağlantılar). */
    brand: "#1a4f8c",
    /** Ana rengin koyusu (üzerine gelme, vurgulu yüzey). */
    "brand-strong": "#123a69",
    /** Ana rengin açığı (seçili zemin, ikon zemini). */
    "brand-soft": "#dce8f6",
    /** Ana rengin en açığı (üzerine gelme zemini). */
    "brand-tint": "#eff5fc",
    /** İkincil renk: şifa yeşili-turkuaz (işaretteki artı, küçük vurgular). */
    accent: "#0b6c63",
    "accent-soft": "#dcf2ee",
    /** Sayfa zemini ve kart yüzeyi. */
    paper: "#f3f6fa",
    surface: "#ffffff",
    /** Metin: ana, ikincil, üçüncül. */
    ink: "#0f1f33",
    "ink-2": "#44546a",
    "ink-3": "#5d6b80",
    /** Çizgiler. */
    line: "#dfe5ee",
    "line-strong": "#c7d1de",
    /** Masaüstünde telefon çerçevesinin arkası. */
    desk: "#d9e1ec",
    /** Klavye odak halkası (ana renkten ayrışsın). */
    focus: "#c2410c",
  },
} as const;

export type BrandColor = keyof typeof BRAND.colors;

/** `:root { --brand: …; … }` (derlemede index.html'e gömülür; ilk boyamada renkler hazırdır). */
export function brandCss(): string {
  const vars = Object.entries(BRAND.colors)
    .map(([name, value]) => `--${name}:${value}`)
    .join(";");
  return `:root{${vars}}`;
}

/** PWA manifest'i (dev sunucusu ve derleme aynı içeriği verir). */
export function brandManifest() {
  return {
    name: `${BRAND.appName} — ${BRAND.fullName}`,
    short_name: BRAND.appName,
    description: BRAND.description,
    lang: "tr",
    dir: "ltr",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: BRAND.colors.paper,
    theme_color: BRAND.colors.brand,
    icons: [
      { src: BRAND.logoUrl, sizes: "any", type: "image/svg+xml" },
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
    ],
  };
}

/** `tel:` bağlantısı. */
export const CALL_CENTER_HREF = `tel:${BRAND.callCenter.tel}`;
