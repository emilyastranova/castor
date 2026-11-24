/** Images a shell card can open in with one click (any other image can be typed in). They need
 *  /bin/sh; Castor starts bash where the image has it, else sh. */
export const SHELL_IMAGES = [
	{ id: 'debian', label: 'Debian', image: 'docker.io/library/debian:stable-slim', description: 'bash, apt-get to add tools' },
	{ id: 'alpine', label: 'Alpine', image: 'docker.io/library/alpine:latest', description: 'sh, small; apk to add tools' }
] as const;

/** The preset an image is, if it's one. */
export function shellPreset(image: string | null | undefined) {
	const v = (image ?? '').trim();
	return SHELL_IMAGES.find((p) => p.image === v);
}
