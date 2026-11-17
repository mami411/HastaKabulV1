using System.Reflection;
using HastaKabul.Api.Controllers;
using HastaKabul.Api.Infrastructure;
using HastaKabul.Application.Abstractions.Persistence;
using HastaKabul.Application.Services;
using HastaKabul.Domain.Common;
using HastaKabul.Domain.Enums;
using HastaKabul.Domain.Knowledge;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.AspNetCore.Mvc.Abstractions;
using Microsoft.AspNetCore.Mvc.Filters;
using Microsoft.AspNetCore.Routing;
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Xunit;

namespace HastaKabul.Tests;

/// <summary>GET /api/v1/kb: ai-simplify servisinin RAG kaynagi.</summary>
public class KnowledgeBaseTests
{
    private sealed class FakeKbRepository(IReadOnlyList<KbEntry> entries) : IKbEntryRepository
    {
        public Task<IReadOnlyList<KbEntry>> SearchAsync(IEnumerable<string> terms, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<KbEntry>>([]);

        public Task<IReadOnlyList<KbEntry>> GetByIdsAsync(IEnumerable<Guid> ids, CancellationToken ct = default)
            => Task.FromResult<IReadOnlyList<KbEntry>>(entries.Where(e => ids.Contains(e.Id)).ToList());

        public Task<IReadOnlyList<KbEntry>> ListAllAsync(CancellationToken ct = default)
            => Task.FromResult(entries);
    }

    [Fact]
    public async Task Kayitlar_id_ve_es_anlamlilarla_doner()
    {
        var lansoprazol = new KbEntry("Lansoprazol", "Lansoprazol", "Mide asidini azaltir.", KbEntryKind.Medication, ["lansor"]);
        var dispepsi = new KbEntry("Dispepsi", "Dispepsi (hazimsizlik)", "Hazimsizlik.", KbEntryKind.Diagnosis, ["hazimsizlik"]);
        var service = new KnowledgeBaseService(new FakeKbRepository([lansoprazol, dispepsi]));

        var list = await service.ListAsync();

        Assert.Equal(2, list.Count);
        // Once tanilar, sonra ilaclar.
        Assert.Equal("Dispepsi", list[0].Term);
        Assert.Equal("DIAGNOSIS", list[0].Kind);
        Assert.Equal("MEDICATION", list[1].Kind);
        Assert.Equal(lansoprazol.Id.ToString(), list[1].Id);
        Assert.Equal(["lansor"], list[1].Synonyms);
        Assert.Equal("Mide asidini azaltir.", list[1].Body);
    }

    [Fact]
    public async Task Bos_bilgi_tabani_bos_liste_doner()
    {
        var service = new KnowledgeBaseService(new FakeKbRepository([]));

        Assert.Empty(await service.ListAsync());
    }

    // ---- Yetkilendirme: onceki testler yalniz servis sinifini sinadigindan uc anahtarsiz acilsa fark edilmezdi.

    [Fact]
    public void Kb_ucu_yalniz_servis_anahtariyla_acilir()
    {
        var controller = typeof(KnowledgeBaseController);
        var action = controller.GetMethod(nameof(KnowledgeBaseController.GetAsync))!;

        Assert.NotNull(action.GetCustomAttribute<RequireServiceKeyAttribute>());
        Assert.Null(controller.GetCustomAttribute<AllowAnonymousAttribute>());
        Assert.Null(action.GetCustomAttribute<AllowAnonymousAttribute>());
        // Tek uc ve yalniz GET (salt okunur).
        var uclar = controller.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly);
        var uc = Assert.Single(uclar);
        Assert.NotNull(uc.GetCustomAttribute<HttpGetAttribute>());
    }

    [Theory]
    [InlineData(null)]
    [InlineData("")]
    [InlineData("yanlis-anahtar")]
    [InlineData("DOGRU-ANAHTAR")]   // buyuk/kucuk harf farki kabul edilmez
    public async Task Servis_anahtari_yanlissa_uc_calismaz(string? anahtar)
    {
        var (context, calisti) = FiltreBaglami(anahtar);

        var ex = await Assert.ThrowsAsync<DomainException>(
            () => new RequireServiceKeyAttribute().OnActionExecutionAsync(context, calisti.Next));

        Assert.Equal(ErrorCodes.Unauthorized, ex.Code);
        Assert.False(calisti.Value);
    }

    [Fact]
    public async Task Dogru_servis_anahtariyla_uc_calisir()
    {
        var (context, calisti) = FiltreBaglami("dogru-anahtar");

        await new RequireServiceKeyAttribute().OnActionExecutionAsync(context, calisti.Next);

        Assert.True(calisti.Value);
    }

    private sealed class Bayrak
    {
        public bool Value { get; private set; }

        public Task<ActionExecutedContext> Next()
        {
            Value = true;
            return Task.FromResult<ActionExecutedContext>(null!);
        }
    }

    private static (ActionExecutingContext, Bayrak) FiltreBaglami(string? anahtar)
    {
        var services = new ServiceCollection()
            .AddSingleton<IConfiguration>(new ConfigurationBuilder()
                .AddInMemoryCollection(new Dictionary<string, string?> { ["ServiceKey"] = "dogru-anahtar" })
                .Build())
            .BuildServiceProvider();
        var http = new DefaultHttpContext { RequestServices = services };
        if (anahtar is not null)
            http.Request.Headers[RequireServiceKeyAttribute.HeaderName] = anahtar;

        var actionContext = new ActionContext(http, new RouteData(), new ActionDescriptor());
        var context = new ActionExecutingContext(
            actionContext, new List<IFilterMetadata>(), new Dictionary<string, object?>(), controller: null!);
        return (context, new Bayrak());
    }
}
