using System.Globalization;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Contracts.Voice;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Common;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Ses oturumu uc noktalari. /verify cagrisini arayuz yapar, orkestrator gormez;
/// orkestrator sonucu GET ile okur.
/// </summary>
[ApiController]
[Route("api/v1/voice-sessions")]
public sealed class VoiceSessionsController(
    VoiceSessionService sessions,
    MedicalHistoryService medicalHistory) : ControllerBase
{
    [HttpPost]
    public Task<VoiceSessionView> CreateAsync(CancellationToken ct) => sessions.CreateAsync(ct);

    [HttpGet("{sessionId:guid}")]
    public Task<VoiceSessionView> GetAsync(Guid sessionId, CancellationToken ct)
        => sessions.GetAsync(sessionId, ct);

    /// <summary>
    /// Ekran tus takimindan gelen TC. Yanit yalnizca dogrulandi mi, kacinci deneme
    /// ve HANDOFF gerekip gerekmedigini soyler.
    /// </summary>
    [HttpPost("{sessionId:guid}/verify")]
    public Task<VoiceVerificationResponse> VerifyAsync(
        Guid sessionId, [FromBody] VerifyVoiceSessionRequest request, CancellationToken ct)
        => sessions.VerifyAsync(sessionId, request, ct);

    /// <summary>
    /// Cikis: hasta uygulamasi oturumu kapatir (tarayici cagirir, servis anahtari istemez; oturum kimligi
    /// zaten oturumun anahtaridir). 204 doner; bilinmeyen oturum 404.
    /// </summary>
    [HttpPost("{sessionId:guid}/end")]
    public async Task<IActionResult> EndAsync(Guid sessionId, CancellationToken ct)
    {
        await sessions.EndAsync(sessionId, ct);
        return NoContent();
    }

    /// <summary>
    /// Dogrulanmis (VERIFIED/COMPLETED) oturumdaki hastanin hekim onayli gecmis ozetleri,
    /// yeniden eskiye. Orkestrator cagirir; X-Service-Key ister. limit 1-5, varsayilan 3.
    /// </summary>
    [HttpGet("{sessionId:guid}/medical-history")]
    [RequireServiceKey]
    public Task<MedicalHistoryResponse> GetMedicalHistoryAsync(
        Guid sessionId, [FromQuery] string? limit, CancellationToken ct)
        => medicalHistory.GetAsync(sessionId, ParseLimit(limit), ct);

    // Sayi olmayan deger model binding'e birakilirsa hata zarfi disinda 400 doner; burada cozulur.
    private static int ParseLimit(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
            return MedicalHistoryService.DefaultLimit;

        if (!int.TryParse(value, NumberStyles.None, CultureInfo.InvariantCulture, out var limit))
            throw new DomainException(
                ErrorCodes.ValidationFailed,
                $"limit {MedicalHistoryService.MinLimit} ile {MedicalHistoryService.MaxLimit} arasında bir sayı olmalıdır.");

        return limit;
    }
}
