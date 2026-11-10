# Justifier une absence (étudiant)

1. Appelle `get_absences` pour trouver l'absence et son identifiant. Ne demande jamais l'identifiant à l'étudiant.
2. Vérifie le délai : cinq jours ouvrés après la séance [procedure-absences#1]. S'il est dépassé, dis-le et propose
   un ticket à la scolarité ; n'appelle pas `justify_absence`.
3. S'il manque le motif ou le justificatif (certificat médical, convocation, attestation de transport), demande-le.
4. Appelle `justify_absence` avec un motif qui nomme le justificatif. La carte de confirmation fait foi.
5. Après confirmation, rappelle que la scolarité examine la demande et que l'étudiant sera prévenu.
