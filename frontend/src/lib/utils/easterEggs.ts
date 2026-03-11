// ── Rejection Milestones (warm/motivational) ──────────────────────
const rejectionMilestones = new Map<number, { message: string; emoji: string }>([
	[10, { message: "You're just warming up.", emoji: '🔥' }],
	[
		25,
		{
			message:
				"Thomas Edison failed 1,000 times before the lightbulb. You're ahead of schedule.",
			emoji: '💡'
		}
	],
	[
		50,
		{
			message: 'Halfway to 100 — and after 100 rejections, the yes hits different.',
			emoji: '🎯'
		}
	],
	[
		75,
		{
			message: "At this point, you're basically rejection-proof. Armor level: mythic.",
			emoji: '🛡️'
		}
	],
	[100, { message: '100 rejections. You are now statistically unstoppable.', emoji: '🚀' }],
	[150, { message: "Most people quit at 50. You're built different.", emoji: '💪' }],
	[
		200,
		{
			message: '200 rejections. At this point, companies are rejecting a legend.',
			emoji: '👑'
		}
	]
]);

// ── Empty States (playful) ────────────────────────────────────────
const emptyStates: Record<string, string[]> = {
	queue: [
		'The right job is out there, probably also refreshing its inbox.',
		'No jobs yet. The market is playing hard to get.',
		"Queue's empty. Time to grab coffee while we hunt."
	],
	applications: [
		'Every expert was once a beginner with an empty applications page.',
		'No applications yet. Your future employer is still writing the job post.',
		"Clean slate. The world is your oyster (that hasn't been applied to yet)."
	],
	cv: [
		'No tailored CVs yet. Your base CV is doing its best.',
		"No CVs here yet. We're warming up the LaTeX compiler.",
		'The CV forge awaits its first commission.'
	]
};

// ── Loading Messages (absurd/playful) ─────────────────────────────
const loadingMessages: string[] = [
	'Scanning the internet for your dream job...',
	'Negotiating with job boards on your behalf...',
	'Teaching robots to read job descriptions...',
	"Convincing LinkedIn you're not a bot...",
	'Translating recruiter-speak into English...',
	'Asking the job market to take you seriously...',
	'Bribing the algorithm with good vibes...',
	'Performing dark arts on job listings...',
	'Whispering sweet nothings to APIs...',
	'Pretending to be 47 browser tabs at once...'
];

// ── Batch Completion Messages ─────────────────────────────────────
const batchMessages: Record<string, string[]> = {
	zero_jobs: [
		"The job market took a coffee break. We'll try again tomorrow.",
		'Zero new jobs. Even the bots need a day off.',
		'Nothing new today. Mercury might be in retrograde.'
	],
	success: [
		'Fresh opportunities, served hot.',
		"Your future employer doesn't know it yet, but today might be the day.",
		"New jobs locked and loaded. Let's get picky."
	]
};

// ── CV Generation Toasts ──────────────────────────────────────────
const cvToasts: string[] = [
	"CV sharpened. You're now 3% more hireable (scientifically unverified).",
	"New CV forged. It's dangerous to go alone — take this.",
	'CV tailored. Looking sharp. Literally.',
	'Another CV crafted. Your LaTeX compiler sends its regards.'
];

// ── Profile Completion ────────────────────────────────────────────
const profileMessages: Record<string, { message: string; emoji: string }> = {
	complete: { message: 'Profile complete. You look great on paper.', emoji: '✨' },
	empty: {
		message:
			'A blank profile is like showing up to an interview in pajamas — comfortable, but not ideal.',
		emoji: '👔'
	},
	partial: {
		message: "Getting there! A few more fields and you'll be unstoppable.",
		emoji: '📝'
	}
};

// ── Apply Confirmation ────────────────────────────────────────────
const applyConfirmation: string[] = [
	'One small click for you, one giant leap for your career.',
	"This application is about to make someone's hiring pipeline very happy.",
	"Ready to make this recruiter's day? Hit the button."
];

// ── Error / 404 Messages ──────────────────────────────────────────
const errorMessages: string[] = [
	"This page is like that perfect job listing — it doesn't exist (yet).",
	'Error 404: Job satisfaction not found. Keep looking.',
	"You've wandered off the career path. Let's get you back.",
	'This page took a personal day. Try another route.'
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
