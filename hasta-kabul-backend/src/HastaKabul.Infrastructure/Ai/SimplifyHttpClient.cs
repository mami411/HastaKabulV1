using System.Net.Http.Json;
using HastaKabul.Application.Abstractions.Ai;
using Microsoft.Extensions.Logging;
using Microsoft.Extensions.Options;

namespace HastaKabul.Infrastructure.Ai;

public sealed class SimplifyOptions
{
    public const string SectionName = "Simplify";

    /// <summary>Sadelestirme servisi: http://ai-simplify:8001</summary>
    public string BaseUrl { get; set; } = "http://localhost:8001";

    /// <summary>Servisler arasi kimlik: X-Service-Key.</summary>
    public string ServiceKey { get; set; } = string.Empty;

    public int TimeoutSeconds { get; set; } = 10;
}

/// <summary>
/// POST :8001/simplify cagrisi. Govde yalnizca beyaz listedeki alanlari tasir:
/// patientRef, diagnosis, medications[], requestId, summaryId. Servis 202 doner;
/// ozet hazir oldugunda PUT /api/v1/summaries/{summaryId} ile geri cagirir.
/// </summary>
public sealed class SimplifyHttpClient(
    HttpClient http,
    IOptions<SimplifyOptions> options,
    ILogger<SimplifyHttpClient> logger) : ISimplifyClient
{
    public async Task<bool> RequestSimplificationAsync(SimplifyRequest request, CancellationToken ct = default)
    {
        var payload = new
        {
            patientRef = request.PatientRef.ToString(),
            diagnosis = request.Diagnosis,
            medications = request.Medications,
            requestId = request.RequestId,
            summaryId = request.SummaryId.ToString()
        };

        try
        {
            using var message = new HttpRequestMessage(HttpMethod.Post, "/simplify")
            {
                Content = JsonContent.Create(payload)
            };
            message.Headers.Add("X-Service-Key", options.Value.ServiceKey);
            message.Headers.Add("X-Request-Id", request.RequestId);

            using var response = await http.SendAsync(message, ct);
            if (response.IsSuccessStatusCode)
                return true;

            // Govde loglanmaz: istek hicbir kisisel tanimlayici tasimasa da kural tektir.
            logger.LogWarning(
                "Sadelestirme servisi {StatusCode} dondu. requestId={RequestId}",
                (int)response.StatusCode,
                request.RequestId);

            return false;
        }
        catch (Exception ex) when (ex is HttpRequestException or TaskCanceledException)
        {
            logger.LogWarning(
                ex,
                "Sadelestirme servisine ulasilamadi. requestId={RequestId}",
                request.RequestId);

            return false;
        }
    }
}
