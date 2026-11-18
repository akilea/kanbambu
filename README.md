# kanbambu

Petit kanban auto-hébergé pour les impressions 3D. Une carte = une impression à faire, un projet = une catégorie.
Aucune connexion au cloud Bambu Lab ni à l'imprimante.

```
Park → Analysed → Printing → Done (✓ / ✗)
```

## Lancer

```bash
docker compose up -d --build
# http://<serveur>:8080
```

Les données (SQLite + fichiers) vivent dans `./data`. Pour sauvegarder, copie ce dossier.

Sans Docker :

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

## Utilisation

| Action | Comment |
| --- | --- |
| Nouveau projet | bouton ➕ en haut |
| Image, lien, fichier Bambu, BOM du projet | ✏️ sur la ligne du projet |
| Réordonner les projets | glisser la poignée ⋮⋮ |
| Plier / déplier | clic sur l'en-tête du projet |
| Nouvelle carte | ➕ sous Park, **ou glisser des STL/3MF sur la colonne** (une carte par fichier) |
| Ajouter un fichier à une carte | glisser le fichier sur la carte, ou dans sa fiche |
| Déplacer | glisser la carte entre les colonnes |

### Analysed, automatiquement

Dans Bambu Studio, tranche la plaque puis **File → Export → Export plate sliced file** (`.gcode.3mf`).
Glisse ce fichier sur la carte : kanbambu lit le temps, le poids et les grammes par couleur, prend la
miniature comme aperçu, et passe la carte de Park à Analysed.

Un `.3mf` de projet normal ne contient pas les grammes. Il donne seulement la miniature.

L'en-tête affiche le total de filament par couleur pour tout ce qui est en Analysed et Printing (plateaux restants).

### Copies et plateaux

Dans la fiche d'une carte : 📦 nombre de copies à imprimer, ▦ copies par plateau. kanbambu en déduit le nombre
de plateaux (affiché `fait/total` sur la carte). Quand un `.gcode.3mf` contient plusieurs objets, les copies par
plateau sont remplies automatiquement (modifiable).

Chaque ✓ dans Done compte un plateau ; un ✗ ne compte pas. Tant qu'il reste des plateaux, la carte en Done a une
bordure pointillée orange et un bouton 🖨️+ qui la renvoie directement dans Printing (pas besoin d'expliquer un
changement : ce n'est pas un réessai). Glisser la carte hors de Done fait la même chose. Le temps, le poids et le
total de filament de l'en-tête comptent seulement les plateaux restants.

Une carte = un plateau tranché (`Export plate sliced file`).

### Done et réessais

- Entrer dans **Done** demande ✓ (amélioration possible, optionnelle) ou ✗ (raison obligatoire).
- Ressortir une carte de Done = réessai : il faut dire **ce qui change**. Ce texte est attaché à la tentative suivante.
- Les compteurs ✓ / ✗ s'affichent sur la carte et sur le projet. L'historique est dans la fiche de la carte.

### Ouvrir dans Bambu Studio

Le bouton ↗ à côté d'un `.3mf` ouvre un lien `bambustudio://open?file=…` (Windows/Linux) ou
`bambustudioopen://…` (macOS). Bambu Studio télécharge alors le fichier depuis kanbambu. Pour ça :

- Bambu Studio doit être le programme associé à ces liens (c'est le cas s'il ouvre les modèles MakerWorld) ;
- le PC doit pouvoir joindre le serveur à l'adresse affichée dans le navigateur (utilise l'IP ou le nom du NAS, pas `localhost`).

Sinon, le bouton ⬇ télécharge le fichier.

## Sécurité

Aucune authentification : à garder sur le réseau local (ou derrière un reverse proxy avec auth).

## Tests

```bash
pip install pytest httpx
KANBAMBU_DATA=$(mktemp -d) python -m pytest tests
```

## Structure

```
app/main.py      API FastAPI + fichiers statiques
app/db.py        SQLite (projects, cards, files, attempts)
app/threemf.py   lecture des .3mf / .gcode.3mf Bambu
static/          interface (HTML/CSS/JS sans build, SortableJS, icônes Lucide)
```
