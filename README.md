# Limfjordssekretariatet QGIS Plugins

Fælles plugin-repository med værktøjer udviklet af Limfjordssekretariatet til brug i QGIS.

## Tilgængelige plugins

| Plugin | Version | Beskrivelse |
|--------|---------|-------------|
| **Limf_SoilSurvey** | 2.0.0 | Håndtering af jordprøver – grid, centerpunkter, QField-klargøring og rapporteksport |
| **Limf_WetlandTools** | 4.0.3 | Diverse GIS-værktøjer til Limfjordssekretariatet |
| **Limf_DrainOutletAnalysis** | 0.1 | Find drænudløbspunkter baseret på fald og DEM |

---

## Installation i QGIS

### Trin 1 – Tilføj repository

1. Åbn QGIS
2. Gå til **Plugins → Manage and Install Plugins**
3. Vælg fanen **Settings**
4. Klik **Add** under *Plugin Repositories*
5. Udfyld felterne:
   - **Name:** `Limfjordssekretariatet`
   - **URL:** `(https://limfjordssekretariatet.github.io/Limfjordssekretariatet_plugins/plugins.xml)`
6. Klik **OK**

### Trin 2 – Installér plugin

1. Gå til fanen **All**
2. Søg efter det ønskede plugin (f.eks. *Jordprover*)
3. Klik **Install Plugin**

> Plugins vises fremover under fanen **Installed**, og QGIS giver besked når der er en ny version tilgængelig.

---

## Opdatering

Når en ny version er udgivet, vil QGIS vise en opdateringsknap under **Plugins → Manage and Install Plugins → Upgradeable**.

---

## Spørgsmål og fejlrapportering

Opret en sag under [Issues](https://github.com/hrosenskjold/Limfjordssekretariatet_plugins/issues).
