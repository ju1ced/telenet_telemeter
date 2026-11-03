# Tickets en sprints synchroniseren

GitHub is de bron voor ticketinhoud en planning. `tickets.md` en `kanban.md` zijn
gegenereerde weergaven: wijzig ze niet rechtstreeks. Maak of bewerk een GitHub
issue met een titel als `TM-17: Onderwerp`. Scope, afhankelijkheden en
acceptatiecriteria staan in de issuebeschrijving. Pull requests worden niet als
tickets opgenomen.

Gebruik deze labels voor de planning:

| Gegeven | GitHub-veld |
| --- | --- |
| Prioriteit | `P0`, `P1` of `P2` |
| Fase | `phase:1` t/m `phase:5` |
| Backlog | `status:backlog` (ook de standaard zonder statuslabel) |
| In uitvoering | `status:in-progress` |
| Review/validatie | `status:review` |
| Geblokkeerd | `status:blocked` |
| Klaar | Issue sluiten |
| Sprint | Issue aan een GitHub milestone koppelen |

Gebruik één statuslabel tegelijk. Gesloten issues staan altijd bij Klaar;
bij meerdere statuslabels geldt daarna Geblokkeerd, Review, In uitvoering.
Het sprintoverzicht toont de milestones en telt uitsluitend de TM-tickets.
Verwijderde issues verdwijnen uit de bestanden; heropende issues keren terug
naar hun huidige statuslabel. Verwijzingen naar TM-tickets worden issue-links.

## Automatisch verversen

Na het mergen van de synchronisatie-PR naar `main` werkt de workflow
**Sync ticket documents** de bestanden bij als issues of milestones veranderen.
Hij draait ook dagelijks en via **Actions → Sync ticket documents → Run workflow**.
De workflow commit alleen gewijzigde documenten; updates zijn dus niet direct
zichtbaar maar volgen zodra de workflow is afgerond.

GitHub Actions moet op deze fork geactiveerd zijn. Als er geen workflowruns
verschijnen, controleer als repositorybeheerder de Actions-tab op de
fork-bevestiging om workflows uit te voeren. De workflow heeft `contents: write`
nodig; branchregels moeten de bot toestaan documentupdates naar `main` te pushen.
Als dat niet mag, ververs lokaal en dien de wijzigingen via een PR in.
Een andere workflow die issues wijzigt met de standaard `GITHUB_TOKEN` start deze
workflow niet opnieuw; de dagelijkse of handmatige run haalt die wijzigingen op.

## Lokaal verversen

Vereist Python 3 en een aangemelde GitHub CLI (`gh auth login`):

```bash
python3 scripts/sync_tickets.py --repository ju1ced/telenet_telemeter
python3 scripts/sync_tickets.py --repository ju1ced/telenet_telemeter --check
```

De eerste opdracht schrijft beide bestanden; de tweede controleert alleen en
geeft een foutcode als ze verouderd zijn. Alle API-pagina's worden opgehaald,
inclusief gesloten issues en milestones. Bij dubbele TM-nummers of een lege
ticketlijst worden de bestanden niet overschreven.

Een bestaande checkout ontvangt botcommits pas na een gewone Git pull. De losse
checkout `/projects/Telenet-Telemeter` bevat een eigen lokale ticketcommit; die
wordt door deze synchronisatie niet automatisch aangepast of overschreven.
Integreer die lokale commit bewust voordat je de wijzigingen van `main` ophaalt.
