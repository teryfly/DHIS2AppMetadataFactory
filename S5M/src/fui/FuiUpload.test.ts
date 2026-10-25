import { describe, expect, it } from 'vitest';
import { mount } from '@vue/test-utils';
import FuiUpload from './FuiUpload.vue';

// The template renders `$t(...)` from the app-wide vue-i18n install a unit mount does not have.
const MOUNT_OPTIONS = { global: { mocks: { $t: (key: string) => key } } };

// `defineExpose` has to sit inside `<script setup>`: written after the closing `</script>` it is
// top-level SFC content, which the compiler drops — the component then exposes nothing, and the
// page's `uploader.pickFile()` was a TypeError instead of the system picker (SRS §6.1 budgets the
// upload → pick-file flow at 2 clicks, so that call is the primary entry point).
describe('FuiUpload 的对外接口（SRS §6.1 两步上传）', () => {
  it('exposes the picker handles a page reaches for', () => {
    const wrapper = mount(FuiUpload, { props: { uploading: false }, ...MOUNT_OPTIONS });
    const exposed = wrapper.vm as unknown as { pickFile?: unknown; pickPhoto?: unknown; pickAlbum?: unknown };
    expect(typeof exposed.pickFile).toBe('function');
    expect(typeof exposed.pickPhoto).toBe('function');
    expect(typeof exposed.pickAlbum).toBe('function');
  });

  it('pickFile opens the file input, not the camera one', async () => {
    const wrapper = mount(FuiUpload, { props: { uploading: false }, ...MOUNT_OPTIONS });
    const opened: string[] = [];
    const inputs = wrapper.findAll('input[type="file"]');
    inputs.forEach((input, i) => {
      const el = input.element as HTMLInputElement;
      el.click = () => {
        opened.push(String(i));
      };
    });
    (wrapper.vm as unknown as { pickFile: () => void }).pickFile();
    // camera / album / file — the bare `<input type="file">` is the last of the three.
    expect(opened).toEqual([String(inputs.length - 1)]);
  });
});
