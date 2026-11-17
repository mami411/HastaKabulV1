import basicSsl from '@vitejs/plugin-basic-ssl'
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv, type Plugin } from 'vite'
import { BRAND, brandCss, brandManifest } from './src/brand.ts'

const escapeHtml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

/**
 * Kurum markası (src/brand.ts) → index.html başlığı, açıklama, tema rengi, favicon, CSS renk değişkenleri
 * ve PWA manifest'i. Böylece yeniden markalamada yalnız brand.ts ve logo dosyası değişir.
 */
function kurumMarkasi(): Plugin {
  const manifest = JSON.stringify(brandManifest(), null, 2)
  return {
    name: 'kurum-markasi',
    transformIndexHtml() {
      return [
        { tag: 'title', children: escapeHtml(`${BRAND.appName} — ${BRAND.fullName}`), injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'description', content: BRAND.description }, injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'theme-color', content: BRAND.colors.brand }, injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'application-name', content: BRAND.appName }, injectTo: 'head' },
        { tag: 'meta', attrs: { name: 'apple-mobile-web-app-title', content: BRAND.appName }, injectTo: 'head' },
        { tag: 'link', attrs: { rel: 'icon', type: 'image/svg+xml', href: BRAND.logoUrl }, injectTo: 'head' },
        { tag: 'style', children: brandCss(), injectTo: 'head-prepend' },
      ]
    },
    configureServer(server) {
      server.middlewares.use('/manifest.webmanifest', (_req, res) => {
        res.setHeader('Content-Type', 'application/manifest+json; charset=utf-8')
        res.end(manifest)
      })
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'manifest.webmanifest', source: manifest })
    },
  }
}

/**
 * `npm run dev`        → yalnız bu bilgisayar (http://localhost:5173), arka uçlara doğrudan gider.
 * `npm run dev:mobil`  → telefon demo modu (--mode mobil): ağa açık, yerel HTTPS (öz imzalı sertifika),
 *                         /api/v1/voice-sessions… → backend (yalnız bu uçlar), /orchestrate /stt /tts →
 *                         ses servisi vekille iletilir.
 *                         Telefon yalnız https://<bilgisayar-ip>:5174 ile konuşur (mikrofon HTTPS ister).
 * `npm run preview:mobil` → aynısı, derlenmiş sürümle (`build:mobil` sonrası; HMR yok).
 */
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const mobil = mode === 'mobil'
  const apiTarget = env.HK_API_TARGET || 'http://127.0.0.1:3000'
  const voiceTarget = env.HK_VOICE_TARGET || 'http://127.0.0.1:8002'
  const port = Number(env.HK_MOBIL_PORT) || 5174

  const mobilServer = {
    host: true,
    port,
    strictPort: true,
    proxy: {
      // Yalnız tarayıcının kullandığı ses oturumu uçları (oluştur, durum, TC doğrula). Tüm /api'yi iletmek
      // backend'i (parolasız hekim girişi, hekim/portal uçları) aynı Wi-Fi'deki herkese açardı; backend
      // kendisi yalnız localhost'u dinler. Diğer /api yolları vekile girmez (Vite'ın kendi yanıtı döner).
      '^/api/v1/voice-sessions(/[0-9a-fA-F-]{36}(/verify)?)?/?$': { target: apiTarget },
      // Asistansız sayfaların hasta uçları ve çıkış (/end; oturuma bağlı; yalnız bu yollar, sorgu dizesiyle).
      // Servis anahtarlı /medical-history, hekim ve portal uçları bilerek dışarıda kalır.
      '^/api/v1/voice-sessions/[0-9a-fA-F-]{36}/(profile|clinics|slot-days|slots|appointments|visits|end)(\\?[^/]*)?$':
        { target: apiTarget },
      '/orchestrate': { target: voiceTarget },
      '/stt': { target: voiceTarget },
      '/tts': { target: voiceTarget },
    },
  }

  return {
    plugins: [react(), kurumMarkasi(), ...(mobil ? [basicSsl({ name: 'hasta-kabul-demo' })] : [])],
    server: mobil ? mobilServer : undefined,
    // `npm run preview:mobil`: derlenmiş sürümü aynı vekil ve HTTPS ile sunar (HMR yok, daha akıcı demo)
    preview: mobil ? mobilServer : undefined,
  }
})
