using HastaKabul.Application.Services;
using Microsoft.Extensions.DependencyInjection;

namespace HastaKabul.Application;

public static class DependencyInjection
{
    /// <summary>
    /// Application katmani yalnizca kendi servislerini kaydeder; port'larin gerceklenmesi
    /// Infrastructure katmanindan gelir (onion: ici disi bilmez).
    /// </summary>
    public static IServiceCollection AddApplication(this IServiceCollection services)
    {
        services.AddScoped<KioskService>();
        services.AddScoped<DoctorPanelService>();
        services.AddScoped<ClinicalEntryService>();
        services.AddScoped<SummaryGenerationService>();
        services.AddScoped<SummaryService>();
        services.AddScoped<SlotService>();
        services.AddScoped<VoiceSessionService>();
        services.AddScoped<AppointmentBookingService>();
        services.AddScoped<PortalService>();
        services.AddScoped<KnowledgeBaseService>();
        services.AddScoped<MedicalHistoryService>();
        services.AddScoped<PatientAppService>();

        return services;
    }
}
