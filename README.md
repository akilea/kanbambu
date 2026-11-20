# kanbambu

Petit kanban auto-hébergé pour les impressions 3D. Une carte = une impression à faire, un projet = une catégorie.
Aucune connexion au cloud Bambu Lab ni à l'imprimante.

```
Queue → Printing → Done (✓ / ✗), avec les pièces du projet à gauche
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
| Nouvelle carte | ➕ sous Queue, **ou glisser des STL/3MF sur la colonne** (une carte par fichier) |
| Ajouter un fichier à une carte | glisser le fichier sur la carte, ou dans sa fiche |
| Déplacer | glisser la carte entre les colonnes |

### Slicing, automatiquement

Dans Bambu Studio, tranche la plaque puis **File → Export → Export plate sliced file** (`.gcode.3mf`).
Glisse ce fichier sur la carte : kanbambu lit le temps, le poids et les grammes par couleur, prend la
miniature comme aperçu.

Un `.3mf` de projet normal ne contient pas les grammes. Il donne seulement la miniature.

L'en-tête affiche le total de filament par couleur pour tout ce qui est en Queue et Printing (plateaux restants).

### Projet Bambu : une carte par plateau

Dans la fiche du projet (✏️), ajoute le `.3mf` du projet Bambu Studio. kanbambu lit tous ses plateaux et crée
une carte par plateau, avec le nom du plateau et l'aperçu de Bambu Studio. Les plateaux déjà tranchés passent
avec leur temps et leurs grammes. Pour avoir les stats de tous les plateaux, fais **Trancher tout**
puis enregistre le projet avant de l'ajouter.

La fiche du projet montre alors les totaux, comme le panneau « All Plates Stats » de Bambu Studio : filament
par emplacement (m, g, coût), temps par plateau, total et reste. L'en-tête du projet affiche le temps et le
poids qui restent.

Après une modification dans Bambu Studio, remplace le fichier (⬆) : les cartes existantes sont mises à jour
(aperçu, temps, grammes) sans doublons, et leur historique est gardé.

### Pièces et exemplaires

Pour un projet imprimé plusieurs fois (2 AMS holder, un exemplaire par enfant…), kanbambu compte des **pièces**,
pas des plateaux :

- Les **colonnes suivent les impressions** : une carte = un plateau lancé, avec sa composition (« 3 Bras + 1 Clip »).
- Le **projet garde l'inventaire** : pour chaque pièce, produites (vert), dans le pipeline = cartes pas encore en Done
  (bleu), et `+N` à planifier. L'en-tête montre le total 🧩 produites/à produire.
- **Exemplaires ×N** (fiche du projet) : multiplie les besoins. Chaque pièce a sa quantité par exemplaire.

D'où viennent les pièces :
- En ajoutant le `.3mf` du projet, chaque objet devient une pièce (quantité = nombre d'instances dans le projet),
  et chaque carte de plateau reçoit sa composition.
- En glissant un `.gcode.3mf` sur un projet qui a des pièces, la composition est lue des noms d'objets du plateau
  (les objets ignorés à l'impression ne comptent pas). Un objet inconnu crée une pièce.
- Renommer une pièce garde l'ancien nom comme alias : les noms d'objets de Bambu Studio continuent de correspondre.
- Sinon, les boutons − / + dans la fiche de la carte.

À l'arrivée dans **Done**, ✓ propose un compteur par pièce : « 2 Bras sur 3 » = réussite partielle (bordure
pointillée orange). Seules les pièces réussies comptent. Le bouton ↻ renvoie la carte dans Queue avec **seulement les
pièces manquantes**, en demandant ce qui change. ⧉ (fiche de la carte) duplique une impression pour la refaire.

Done garde les 3 cartes les plus récentes par projet ; `+N` affiche les autres.

### Copies et plateaux (cartes simples)

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
- Ressortir de Done une carte **en échec** = réessai : il faut dire **ce qui change**. Ce texte est attaché à la
  tentative suivante. Après une réussite (complète ou partielle), la carte repart sans question.
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
app/db.py        SQLite (projects, parts, cards, card_parts, files, attempts)
app/threemf.py   lecture des .3mf / .gcode.3mf Bambu (plateaux, temps, filament, coût, aperçus)
static/          interface (HTML/CSS/JS sans build, SortableJS, icônes Lucide)
```
