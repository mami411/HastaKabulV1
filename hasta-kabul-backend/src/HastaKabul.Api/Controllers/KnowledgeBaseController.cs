using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Contracts.Knowledge;
using HastaKabul.Application.Services;
using Microsoft.AspNetCore.Mvc;

namespace HastaKabul.Api.Controllers;

/// <summary>
/// Bilgi tabani (RAG kaynagi). Yalnizca ai-simplify servisi X-Service-Key ile okur;
/// tarayiciya acik degildir.
/// </summary>
[ApiController]
[Route("api/v1/kb")]
public sealed class KnowledgeBaseController(KnowledgeBaseService knowledgeBase) : ControllerBase
{
    [HttpGet]
    [RequireServiceKey]
    public Task<IReadOnlyList<KbEntryView>> GetAsync(CancellationToken ct)
        => knowledgeBase.ListAsync(ct);
}
