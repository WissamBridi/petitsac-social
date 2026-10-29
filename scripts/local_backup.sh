#!/bin/zsh
# Secours local (Mac) de la publication Instagram : GitHub Actions retarde ou saute ses créneaux planifiés.
# Lancé toutes les 10 min par launchd (com.petitsac.social.backup). Décide en heure UTC (= heure du Sénégal) :
#   Reel du jour à partir de 16 h 00, photo à partir de 19 h 00 (plages dans publish.py), toutes les 10 min jusqu'à ce qu'ils soient en ligne.
# Aucun doublon : publish.py vérifie le journal ET le fil Instagram avant de publier.
cd "$HOME/petitsac-social" || exit 1
H=$(date -u +%H%M)
[ "$H" -lt 1600 ] && exit 0
set -a; source "$HOME/petit-sac-insta/.env"; set +a
export BASE_URL="https://wissambridi.github.io/petitsac-social/images/"
git pull -q --rebase --autostash origin main || exit 1
KINDS=(post reel)
for K in $KINDS; do
  echo "--- $(date -u '+%F %T') UTC · file $K"
  KIND=$K /usr/bin/python3 scripts/publish.py
done
if [ -n "$(git status --porcelain content/published.json)" ]; then
  git add content/published.json
  git commit -qm "chore: publication par le secours local le $(date -u +%F)"
  git pull -q --rebase --autostash origin main && git push -q origin main
fi
