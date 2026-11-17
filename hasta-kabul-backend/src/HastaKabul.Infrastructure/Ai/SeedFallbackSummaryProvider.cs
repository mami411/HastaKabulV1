using System.Text.RegularExpressions;
using HastaKabul.Application.Abstractions.Ai;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Summaries;
using HastaKabul.Infrastructure.Persistence.Seed;

namespace HastaKabul.Infrastructure.Ai;

/// <summary>
/// Sadelestirme servisine ulasilamazsa (ya da geri cagri zaman asimina ugrarsa) kullanilan, onceden
/// yazilmis yedek ozetler. Seed bilgi tabanindaki 8 taninin hepsini kapsar; metinler hastaya gider
/// ve dogru Turkce karakterlerle yazilir. Kurallar:
/// <list type="bullet">
/// <item>Ilac listesi her zaman HEKIMIN GIRDISINDEN uretilir; yedek metin ilac uydurmaz. Bilgi
/// tabaninda olan ilac icin amac/kullanim ipucu eklenir, olmayan ilac adiyla birlikte "hekiminize
/// sorun" notuyla doner ve unknownTerms'e yazilir (hekim onaydan once sari kutuda gorur).</item>
/// <item>Doz yalniz hekimin yazdigindan cikarilir ("2x1" -> "Günde 2 kez 1 tane"); yazilmadiysa
/// "hekiminizin önerdiği şekilde" denir.</item>
/// <item>Tani bolumlerinden hicbiri taninmazsa yedek yoktur (null) ve kayit FAILED/LLM_UNAVAILABLE olur.</item>
/// </list>
/// Eslestirme kulturden bagimsizdir (<see cref="TurkishText.Fold"/>): "DİSPEPSİ", "hazımsızlık" ayni.
/// </summary>
public sealed class SeedFallbackSummaryProvider : IFallbackSummaryProvider
{
    private sealed record DiagnosisText(string Term, string[] Keys, string Plain, string[] Warnings);

    private sealed record MedicationText(string Term, string Name, string[] Keys, string Purpose, string Tip);

    private static readonly DiagnosisText[] Diagnoses =
    [
        new("Dispepsi", ["dispepsi", "hazimsizlik"],
            "Mideniz fazla asit üretiyor ve bu yüzden yanma, şişme gibi şikayetler oluyor. " +
            "Ciddi bir hastalık değil; ilacınızı düzenli kullanırsanız şikayetleriniz azalır.",
            ["Şikayetleriniz iki hafta içinde geçmezse hekiminize tekrar başvurun."]),
        new("Gastrit", ["gastrit", "mide iltihabi"],
            "Midenizin iç yüzeyi tahriş olmuş (mide iltihabı). Bu durum yanma ve ağrıya yol açar. " +
            "Acı, baharatlı yiyecekleri ve sigarayı azaltmak, ilacınızı düzenli kullanmak iyileşmeye yardımcı olur.",
            ["Siyah renkli dışkı ya da kanlı kusma olursa hemen acile başvurun."]),
        new("Hipertansiyon", ["hipertansiyon", "yuksek tansiyon"],
            "Tansiyonunuz sürekli olarak yüksek seyrediyor (yüksek tansiyon). Tuzu azaltmak ve " +
            "ilacınızı her gün düzenli kullanmak kalbinizi ve damarlarınızı korur.",
            ["Evde ölçtüğünüz tansiyon değerlerini kaydedin.", "Günlük tuz tüketiminizi azaltın."]),
        new("Tip 2 Diyabet", ["tip 2 diyabet", "diyabet", "seker hastaligi"],
            "Kan şekeriniz olması gerekenden yüksek seyrediyor (tip 2 diyabet, şeker hastalığı). " +
            "Düzenli ilaç kullanımı, beslenme düzeni ve hareketle kontrol altına alınabilir.",
            ["Evde ölçtüğünüz kan şekeri değerlerini not edip kontrole getirin."]),
        new("Demir Eksikliği Anemisi", ["demir eksikligi", "anemi", "kansizlik"],
            "Kanınızdaki demir deposu azalmış (kansızlık). Halsizlik ve solukluk bundan kaynaklanıyor " +
            "olabilir. Demir ilacı ve demirden zengin beslenmeyle depolar yeniden dolar.",
            ["Demir ilacı dışkınızın rengini koyulaştırabilir; bu beklenen bir durumdur."]),
        new("Migren", ["migren"],
            "Baş ağrınız migrene bağlı. Migren genellikle başın bir tarafında zonklayıcı ağrı yapar; " +
            "ışık ve ses rahatsız edebilir. Ağrıyı tetikleyen durumları (uykusuzluk, açlık, stres) " +
            "fark edip azaltmak atakları seyrekleştirir.",
            ["Hayatınızın en şiddetli baş ağrısı, konuşma bozukluğu ya da güç kaybı olursa hemen acile başvurun."]),
        new("Akut Sinüzit", ["akut sinuzit", "sinuzit"],
            "Burnunuzun çevresindeki boşluklar (sinüsler) iltihaplanmış. Bu durum yüzde basınç hissi ve " +
            "burun tıkanıklığı yapar. Bol sıvı almak ve dinlenmek iyileşmeyi hızlandırır.",
            ["Ateşiniz üç günden uzun sürerse ya da gözünüzün çevresinde şişlik olursa hekiminize başvurun."]),
        new("Hipotiroidi", ["hipotiroidi", "hipotiroidizm", "tiroid bezinin az calismasi"],
            "Boynunuzdaki tiroid bezi yeterince hormon üretmiyor (tiroid bezinin az çalışması). " +
            "Bu durum halsizlik, üşüme ve kilo artışı yapabilir. Eksik hormon ilaçla tamamlanır.",
            ["Tiroid değerleriniz için kontrol kan tahlillerini aksatmayın."])
    ];

    private static readonly MedicationText[] Medications =
    [
        new("Lansoprazol", "Lansoprazol", ["lansoprazol", "lansor"], "Mide asidini azaltır.",
            "Kahvaltıdan yarım saat önce, aç karnına alın."),
        new("Famotidin", "Famotidin", ["famotidin"], "Mide asidini azaltır.",
            "Genellikle akşam yemeğinden sonra alınır."),
        new("Amlodipin", "Amlodipin", ["amlodipin"], "Damarlarınızı gevşeterek tansiyonunuzu düşürür.",
            "Her gün aynı saatte alın."),
        new("Metformin", "Metformin", ["metformin"], "Kan şekerinizi dengeler.",
            "Mide şikayetini azaltmak için yemekle birlikte alın."),
        new("Demir Sülfat", "Demir sülfat", ["demir sulfat", "demir ilaci"], "Azalan demir deponuzu doldurur.",
            "C vitamini içeren meyve suyuyla alın; süt ve çayla birlikte almayın."),
        new("Parasetamol", "Parasetamol", ["parasetamol", "paracetamol"], "Ağrınızı ve ateşinizi azaltır.",
            "Tok karnına alın; günlük dozu aşmayın."),
        new("Amoksisilin", "Amoksisilin", ["amoksisilin", "amoxicillin"], "Bakterilerin yol açtığı enfeksiyonu tedavi eden bir antibiyotiktir.",
            "Kendinizi iyi hissetseniz bile kutuyu yarıda bırakmadan bitirin."),
        new("Levotiroksin", "Levotiroksin", ["levotiroksin", "levothyroxine"], "Eksik tiroid hormonunu yerine koyar.",
            "Sabah aç karnına, bir bardak suyla alın; kahvaltıyı yarım saat sonra yapın.")
    ];

    private const string UnknownMedicationPurpose =
        "Hekiminizin reçete ettiği ilaç; ne işe yaradığını hekiminize ya da eczacınıza sorabilirsiniz.";

    private const string StopWarning = "İlaçlarınızı kendi kararınızla bırakmayın.";

    private static readonly Regex DoseRegex = new(@"(\d+)\s*[x×*]\s*(\d+)", RegexOptions.CultureInvariant);
    private static readonly Regex SegmentSplit = new(@"[;,/\n+]| ve ", RegexOptions.CultureInvariant);

    public Task<FallbackSummary?> FindAsync(
        string diagnosis, IEnumerable<string> medications, CancellationToken ct = default)
        => Task.FromResult(Build(diagnosis, medications));

    public static FallbackSummary? Build(string? diagnosis, IEnumerable<string>? medications)
    {
        if (string.IsNullOrWhiteSpace(diagnosis))
            return null;

        var matched = new List<DiagnosisText>();
        var unknownTerms = new List<string>();

        foreach (var segment in SegmentSplit.Split(diagnosis).Select(s => s.Trim()).Where(s => s.Length > 0))
        {
            var folded = TurkishText.Fold(segment);
            var hit = Diagnoses.FirstOrDefault(d => d.Keys.Any(k => folded.Contains(k, StringComparison.Ordinal)));
            if (hit is null)
                unknownTerms.Add(segment);
            else if (!matched.Contains(hit))
                matched.Add(hit);
        }

        if (matched.Count == 0)
            return null;

        var sourceIds = matched.Select(d => DemoDataSeeder.KnowledgeBaseId(d.Term).ToString()).ToList();
        var meds = new List<SimplifiedMedication>();

        foreach (var raw in (medications ?? []).Select(m => m?.Trim() ?? string.Empty).Where(m => m.Length > 0))
        {
            var folded = TurkishText.Fold(raw);
            var known = Medications.FirstOrDefault(m => m.Keys.Any(k => folded.Contains(k, StringComparison.Ordinal)));
            var (dose, food) = ParseDose(folded);

            if (known is null)
            {
                unknownTerms.Add(raw);
                meds.Add(new SimplifiedMedication
                {
                    Name = raw.Length > 80 ? raw[..80] : raw,
                    Purpose = UnknownMedicationPurpose,
                    Usage = Usage(dose, food, tip: null)
                });
                continue;
            }

            var id = DemoDataSeeder.KnowledgeBaseId(known.Term).ToString();
            if (!sourceIds.Contains(id))
                sourceIds.Add(id);

            meds.Add(new SimplifiedMedication
            {
                Name = known.Name,
                Purpose = known.Purpose,
                // Hekim ac/tok belirttiyse bilgi tabanindaki ipucu eklenmez (celiski olmasin).
                Usage = Usage(dose, food, food is null ? known.Tip : null)
            });
        }

        var warnings = matched.SelectMany(d => d.Warnings).ToList();
        if (meds.Count > 0)
            warnings.Add(StopWarning);

        return new FallbackSummary(
            string.Join(" ", matched.Select(d => d.Plain)),
            meds,
            warnings.Distinct().ToList(),
            unknownTerms.Distinct().ToList(),
            sourceIds);
    }

    /// <summary>"2x1 tok" -> ("Günde 2 kez 1 tane", "tok karnına"). Katlanmis metin uzerinde calisir.</summary>
    private static (string? Dose, string? Food) ParseDose(string folded)
    {
        string? dose = null;
        var match = DoseRegex.Match(folded);
        if (match.Success)
            dose = $"Günde {match.Groups[1].Value} kez {match.Groups[2].Value} tane";

        var tokens = folded.Split([' ', '.', ',', '(', ')'], StringSplitOptions.RemoveEmptyEntries);
        string? food = tokens.Contains("tok") ? "tok karnına"
            : tokens.Contains("ac") ? "aç karnına"
            : null;

        return (dose, food);
    }

    private static string Usage(string? dose, string? food, string? tip)
    {
        var first = (dose, food) switch
        {
            (not null, not null) => $"{dose}, {food} alın.",
            (not null, null) => $"{dose} alın.",
            (null, not null) => $"Hekiminizin önerdiği dozda, {food} alın.",
            _ => "Hekiminizin önerdiği şekilde kullanın."
        };

        return tip is null ? first : $"{first} {tip}";
    }
}
