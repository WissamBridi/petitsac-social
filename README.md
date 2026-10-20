# petitsac-social

Publication Instagram automatique (1 post/jour) pour **@petitsac_sng**, 100 % GitHub (zéro serveur).

- `content/posts.json` : la file d'attente datée (image unique ou carrousel + légende).
- `content/published.json` : journal des posts déjà publiés (mis à jour tout seul).
- `docs/images/` : les visuels, servis publiquement par **GitHub Pages**.
- `scripts/publish.py` : publie le post du jour via l'API Graph (image + carrousel).
- `.github/workflows/daily.yml` : **cron quotidien** (11:00 UTC) + lancement manuel.

## Mise en route (une fois)

1. **Créer le dépôt** sur GitHub (nom conseillé : `petitsac-social`, visibilité *public* pour que Pages serve les images) et y pousser ce dossier.
2. **Activer GitHub Pages** : Settings → Pages → Source = `Deploy from a branch`, branche `main`, dossier `/docs`. L'URL devient `https://<user>.github.io/petitsac-social/`.
3. **Ajouter le secret du token** : Settings → Secrets and variables → Actions → New repository secret → nom `IG_ACCESS_TOKEN`, valeur = le token système non-expirant.
4. **Tester** : onglet Actions → « Publication Instagram quotidienne » → Run workflow → `dry_run = 1` (prépare sans publier). Puis sans dry_run pour publier pour de vrai.

Ensuite, chaque jour à 11:00 UTC, le post daté du jour part tout seul.

## Ajouter des posts

Déposer le(s) visuel(s) dans `docs/images/`, puis ajouter une entrée dans `content/posts.json` :
```json
{ "id": "2026-11-02-mon-post", "date": "2026-11-02", "type": "image",
  "images": ["mon-visuel.png"], "caption": "Mon texte ✦\n\n#PetitSac #Dakar" }
```
Pour un carrousel : `"type": "carousel"` et plusieurs fichiers dans `images` (max 10).

## Changer l'heure
Modifier le `cron` dans `.github/workflows/daily.yml` (en UTC).
