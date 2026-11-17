FROM mcr.microsoft.com/dotnet/sdk:9.0 AS build
WORKDIR /src

# Yalnizca proje dosyalari once kopyalanir: kaynak degisince restore katmani yeniden kullanilir.
COPY src/HastaKabul.Domain/HastaKabul.Domain.csproj src/HastaKabul.Domain/
COPY src/HastaKabul.Application/HastaKabul.Application.csproj src/HastaKabul.Application/
COPY src/HastaKabul.Infrastructure/HastaKabul.Infrastructure.csproj src/HastaKabul.Infrastructure/
COPY src/HastaKabul.Api/HastaKabul.Api.csproj src/HastaKabul.Api/
RUN dotnet restore src/HastaKabul.Api/HastaKabul.Api.csproj

COPY . .
RUN dotnet publish src/HastaKabul.Api/HastaKabul.Api.csproj -c Release -o /app/publish

FROM mcr.microsoft.com/dotnet/aspnet:9.0 AS runtime
WORKDIR /app

# Europe/Istanbul donusumu icin saat dilimi verisi gerekir.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /app/publish .
EXPOSE 3000
ENTRYPOINT ["dotnet", "HastaKabul.Api.dll"]
