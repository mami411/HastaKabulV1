using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Contracts.Summaries;
using HastaKabul.Application.Services;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Sade ozet uc noktalari: AI callback'i (servis anahtari ile) ve hekim onay/ret.
/// </summary>
[ApiController]
[Route("api/v1/summaries")]
public sealed class SummariesController(SummaryService summaries, IDoctorContext doctorContext) : ControllerBase
{
    [HttpGet("{summaryId:guid}")]
    [Authorize]
    public Task<SummaryView> GetAsync(Guid summaryId, CancellationToken ct)
        => summaries.GetAsync(doctorContext.Require(null), summaryId, ct);

    /// <summary>AI servisi ozeti hazirlayinca buraya yazar; kayit READY olur ve olay yayilir.</summary>
    [HttpPut("{summaryId:guid}")]
    [RequireServiceKey]
    public Task<SummaryView> CallbackAsync(
        Guid summaryId, [FromBody] SummaryCallbackRequest request, CancellationToken ct)
        => summaries.ApplyCallbackAsync(summaryId, request, ct);

    [HttpPost("{summaryId:guid}/approve")]
    [Authorize]
    public Task<SummaryDecisionResponse> ApproveAsync(Guid summaryId, CancellationToken ct)
        => summaries.ApproveAsync(doctorContext.Require(null), summaryId, ct);

    [HttpPost("{summaryId:guid}/reject")]
    [Authorize]
    public Task<SummaryDecisionResponse> RejectAsync(Guid summaryId, CancellationToken ct)
        => summaries.RejectAsync(doctorContext.Require(null), summaryId, ct);
}
