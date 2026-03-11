// ── Rejection Milestones (warm/motivational) ──────────────────────
const rejectionMilestones = new Map<number, { message: string; emoji: string }>([
	[10, { message: 'Tu viens à peine de commencer.', emoji: '🔥' }],
	[
		25,
		{
			message:
				"Thomas Edison a échoué 1 000 fois avant l'ampoule. Tu es en avance sur son planning.",
			emoji: '💡'
		}
	],
	[
		50,
		{
			message: 'À mi-chemin des 100 — et après 100 refus, le oui fait encore plus mal aux RH.',
			emoji: '🎯'
		}
	],
	[
		75,
		{
			message: 'À ce stade, tu es pratiquement imperméable aux refus. Niveau armure : mythique.',
			emoji: '🛡️'
		}
	],
	[100, { message: '100 refus. Tu es désormais statistiquement inarrêtable.', emoji: '🚀' }],
	[150, { message: "La plupart abandonnent à 50. Toi t'es d'une autre trempe.", emoji: '💪' }],
	[
		200,
		{
			message: '200 refus. Les entreprises rejettent une légende, maintenant.',
			emoji: '👑'
		}
	]
]);

// ── Empty States (playful) ────────────────────────────────────────
const emptyStates: Record<string, string[]> = {
	queue: [
		"Le job idéal est là quelque part, il rafraîchit sûrement aussi sa boîte mail.",
		'Aucune offre pour l'instant. Le marché fait des caprices.',
		'File vide. Pause café pendant qu'on chasse pour toi.'
	],
	applications: [
		'Tout expert a un jour eu une page de candidatures vide.',
		"Pas encore de candidatures. Ton futur employeur est encore en train d'écrire l'offre.",
		'Page vierge. Le monde t'appartient (il ne t'a pas encore répondu).'
	],
	cv: [
		'Pas encore de CV personnalisé. Ton CV de base fait de son mieux.',
		"Rien ici pour l'instant. On chauffe le compilateur LaTeX.",
		"La forge à CV attend sa première commande."
	]
};

// ── Loading Messages (absurd/playful) ─────────────────────────────
const loadingMessages: string[] = [
	"Scan de l'internet à la recherche de ton job de rêve...",
	'Négociation avec les job boards en ton nom...',
	'Apprentissage de la lecture de fiches de poste par les robots...',
	"Convaincre LinkedIn que tu n'es pas un bot...",
	'Traduction du jargon RH en français courant...',
	"Demander au marché de l'emploi de te prendre au sérieux...",
	"Soudoyer l'algorithme avec de bonnes ondes...",
	'Pratique des arts obscurs sur les offres d\'emploi...',
	"Chuchoter des douceurs aux APIs...",
	'Faire semblant d\'être 47 onglets de navigateur à la fois...'
];

// ── Batch Completion Messages ─────────────────────────────────────
const batchMessages: Record<string, string[]> = {
	zero_jobs: [
		"Le marché de l'emploi fait une pause café. On réessaie demain.",
		'Zéro nouvelle offre. Même les bots ont droit à un jour de repos.',
		"Rien de nouveau aujourd'hui. Mercure est peut-être rétrograde."
	],
	success: [
		'Nouvelles opportunités, servies fraîches.',
		"Ton futur employeur ne le sait pas encore, mais aujourd'hui c'est peut-être le jour.",
		"Nouvelles offres prêtes à l'emploi. Soyons exigeants."
	]
};

// ── CV Generation Toasts ──────────────────────────────────────────
const cvToasts: string[] = [
	"CV affiné. Tu es maintenant 3 % plus employable (non vérifié scientifiquement).",
	"Nouveau CV forgé. C'est dangereux d'y aller seul — prends ça.",
	'CV taillé sur mesure. Tu en jettes. Littéralement.',
	'Encore un CV de forgé. Ton compilateur LaTeX te passe le bonjour.'
];

// ── Profile Completion ────────────────────────────────────────────
const profileMessages: Record<string, { message: string; emoji: string }> = {
	complete: { message: 'Profil complet. Tu en jettes sur le papier.', emoji: '✨' },
	empty: {
		message:
			"Un profil vide, c'est comme arriver à un entretien en pyjama — confortable, mais pas idéal.",
		emoji: '👔'
	},
	partial: {
		message: "On y est presque ! Quelques champs de plus et tu seras inarrêtable.",
		emoji: '📝'
	}
};

// ── Apply Confirmation ────────────────────────────────────────────
const applyConfirmation: string[] = [
	'Un petit clic pour toi, un grand bond pour ta carrière.',
	"Cette candidature va rendre le pipeline de recrutement de quelqu'un très heureux.",
	"Prêt à faire la journée de ce recruteur ? Appuie sur le bouton."
];

// ── Error / 404 Messages ──────────────────────────────────────────
const errorMessages: string[] = [
	"Cette page est comme l'offre d'emploi parfaite — elle n'existe pas (encore).",
	"Erreur 404 : Satisfaction professionnelle introuvable. Continue de chercher.",
	"Tu t'es écarté du chemin de carrière. On te ramène.",
	'Cette page a pris un jour de congé. Essaie une autre route.'
];

// ── Helpers ───────────────────────────────────────────────────────

let lastLoadingIndex = -1;

export function getRandomMessage(messages: string[]): string {
	return messages[Math.floor(Math.random() * messages.length)];
}

export function getLoadingMessage(): string {
	let index: number;
	do {
		index = Math.floor(Math.random() * loadingMessages.length);
	} while (index === lastLoadingIndex && loadingMessages.length > 1);
	lastLoadingIndex = index;
	return loadingMessages[index];
}

export function getRejectionMilestone(
	count: number
): { message: string; emoji: string; isSpecial: boolean } | null {
	const milestone = rejectionMilestones.get(count);
	if (!milestone) return null;
	return { ...milestone, isSpecial: count >= 100 };
}

export function getEmptyState(context: string): string {
	const messages = emptyStates[context];
	if (!messages) return '';
	return getRandomMessage(messages);
}

export function getBatchMessage(outcome: 'zero_jobs' | 'success'): string {
	return getRandomMessage(batchMessages[outcome]);
}

export function getCvToast(): string {
	return getRandomMessage(cvToasts);
}

export function getProfileStatus(fields: Record<string, unknown>): {
	message: string;
	emoji: string;
} {
	const values = Object.values(fields);
	const filled = values.filter((v) => v !== null && v !== undefined && v !== '').length;
	const ratio = filled / values.length;
	if (ratio === 0) return profileMessages.empty;
	if (ratio >= 1) return profileMessages.complete;
	return profileMessages.partial;
}

export function getApplyConfirmation(): string {
	return getRandomMessage(applyConfirmation);
}

export function getErrorMessage(): string {
	return getRandomMessage(errorMessages);
}
