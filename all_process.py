import argparse
import shutil
import zipfile
import gradio as gr
import torchaudio
import json
import os
import tempfile
import soundfile as sf
from moviepy.editor import AudioFileClip
from concurrent.futures import ThreadPoolExecutor
from pydub import AudioSegment
import extern_subprocess
import update_status
from pydub.silence import split_on_silence
temp_folder = tempfile.gettempdir()
current_file_path = os.path.abspath(__file__)
current_directory = os.path.dirname(current_file_path)

# 获取配置中的目标采样率
with open("./configs/config.json", 'r', encoding='utf-8') as f:
    hps = json.load(f)
target_sr = hps['data']['sampling_rate']

taboo_symbols = "{<>}[]abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890"
parser = argparse.ArgumentParser()
parser.add_argument(
    "--share", default=False, help="make link public", action="store_true"
)
parser.add_argument(
    "--target_path", default="./raw/gongzi", help="target path to store wavs", type=str
)
args = parser.parse_args()
lang_dict = {"EN(英文)": "_en", "ZH(中文)": "_zh", "JP(日语)": "_jp"}
inv_lang_dict = {"_en": "EN", "_zh": "ZH", "_jp": "JP"}

def extract_zip(zip_path, extract_path, encoding='gbk'):
    print(zip_path)
    with zipfile.ZipFile(zip_path, 'r') as z:
        for file_info in z.infolist():
            original_file_name = file_info.filename
            print(original_file_name)
            try:
                file_info.filename = file_info.filename.encode('cp437').decode(encoding)
            except Exception as e:
                print('Error: ', e)
            file_info.filename = os.path.basename(file_info.filename)
            print(file_info.filename)
            z.extract(file_info, extract_path)



def voice_detection_with_pydub(
        denoised_audio_path,
        target_path_dir,
        min_wav_len=1,
        max_wav_len=10,
        min_silence_len=1000,
        silence_thresh=-42):
    print(denoised_audio_path, ' ', target_path_dir)
    audio = AudioSegment.from_file(denoised_audio_path)
    segments = split_on_silence(audio, min_silence_len=min_silence_len, silence_thresh=silence_thresh,
                                keep_silence=500, seek_step=20)
    total_len = 0
    for i, segment in enumerate(segments):
        segment_duration = len(segment)
        if segment_duration >= min_wav_len * 1000 and segment_duration <= max_wav_len * 1000:
            output_filename = os.path.join(
                target_path_dir, os.path.basename(denoised_audio_path)
                .replace('.WAV', '.wav').replace('.wav', f'_seg_{i}.wav')
            )
            segment.export(output_filename, format="wav")
            total_len += segment_duration
            print("OK: Duration:", segment_duration / 1000, "seconds")
    total_len /= 1000
    hr, minute, sec = int(total_len / 3600), (int(total_len) % 3600) // 60, int(total_len) % 60
    print(f"切割人声完毕，有效总时长={hr}小时:{minute}分钟:{sec}秒")


def denoise_audio(audio_dir,
                  target_path_dir,
                  silence_thresh=-42,
                  min_silence_len=300,
                  min_wav_len=1,
                  max_wav_len=10,
                  bool_detach=True):
    denoise_audio_dir = os.path.join('./denoised_audio', os.path.basename(target_path_dir))
    os.makedirs(denoise_audio_dir, exist_ok=True)
    os.makedirs(target_path_dir, exist_ok=True)
    filelist = list(os.walk(audio_dir))[0][2]
    print('audio_dir: ', filelist)
    for file in filelist:

        # 提取文件名
        folder_name = os.path.splitext(file)[0]
        # 角色文件名
        character_folder_name = os.path.basename(target_path_dir)
        separated_path = f"./separated/{character_folder_name}/htdemucs/{folder_name}/vocals.wav"
        print('separated_path: ', separated_path)
        if '_seg_' in folder_name:
            if os.path.exists(os.path.join(audio_dir, file)):
                os.remove(os.path.join(audio_dir, file))
                print(f'removed original \"{os.path.join(audio_dir, file)}\"')
            continue
        if os.path.exists(separated_path):
            print(separated_path + " already exists")
        elif file.lower().endswith(".wav"):
            raw_audio_path = os.path.join(audio_dir, file)
            denoise_audio_path = os.path.join(denoise_audio_dir, file)
            if bool_detach:
                if os.name == 'nt':
                    python_path = os.path.abspath(f"./env/python.exe")
                else:
                    python_path = "python"
                demucs_command = (
                        python_path + " " +
                        f"-m demucs --two-stems=vocals \"{raw_audio_path}\" "
                        f"-o \"./separated/{character_folder_name}\""
                )
                os.system(demucs_command)
            if not os.path.exists(denoise_audio_path):
                wav, sr = torchaudio.load(separated_path if bool_detach else raw_audio_path
                                          , frame_offset=0, num_frames=-1,
                                          normalize=True,
                                          channels_first=True)
                wav = wav.mean(dim=0).unsqueeze(0)
                # if sr != target_sr:
                #     wav = torchaudio.transforms.Resample(orig_freq=sr, new_freq=target_sr)(wav)
                torchaudio.save(denoise_audio_path, wav, sr, channels_first=True)

    # 遍历降噪音频文件并提取有效声音片段
    filelist = list(os.walk(denoise_audio_dir))[0][2]
    print('denoised audio: ', filelist)
    origin_filelist = filelist
    for file in filelist:
        if file.lower().endswith(".wav"):
            print(os.path.join(denoise_audio_dir, file))
            # voice_detection_with_librosa(
            #     os.path.join(denoise_audio_dir, file), target_path_dir,
            #     top_db=top_db, min_wav_len=min_wav_len, max_wav_len=max_wav_len
            # )
            voice_detection_with_pydub(
                os.path.join(denoise_audio_dir, file), target_path_dir,
                min_wav_len=min_wav_len, max_wav_len=max_wav_len,
                silence_thresh=silence_thresh, min_silence_len=min_silence_len
            )
    for file in origin_filelist:
        file.replace(".WAV", ".wav")
        target_audio_path = os.path.join(target_path_dir, file)
        if os.path.exists(target_audio_path) and file.endswith(".wav"):
            os.remove(target_audio_path)
    print("音频去噪完毕")


# 递归函数，用于处理文件夹内的所有MP3文件
def convert_mp3_to_wav(folder_path):
    for root, dirs, files in os.walk(folder_path):
        for fname in files:
            if fname.lower().endswith(".mp3"):
                mp3_file_path = os.path.join(root, fname)
                audio = AudioSegment.from_mp3(mp3_file_path)
                wav_fname = os.path.splitext(fname)[0] + ".wav"
                wav_file_path = os.path.join(root, wav_fname)
                audio.export(wav_file_path, format="wav")
                os.remove(mp3_file_path)
                print(f"[MP3 to WAV] {mp3_file_path} converted to {wav_file_path}")
    print("转换mp3到wav完毕")


def slice_audio(target_path, lang, silence_thresh=-42, min_silence_len=300,
                min_wav_len=1, max_wav_len=10, bool_detach="是"):
    min_length_ms = min_wav_len * 1000
    max_length_ms = max_wav_len * 1000
    target_path = update_status.raw_dir_convert_to_path(target_path, lang)
    print("target_path: ", target_path, "lang: ", lang, "silence_thresh: ", silence_thresh,
          "min_silence: ", min_silence_len, "detach: ", bool_detach)
    convert_mp3_to_wav(target_path)
    _bool_detach = True if bool_detach == "是" else False
    keep_file = False if (_bool_detach is True) else True
    # 降噪，去除没有人声的部分
    denoise_audio(target_path, target_path, silence_thresh, min_silence_len, min_wav_len, max_wav_len, _bool_detach)

    for filename in os.listdir(target_path):
        if filename.lower().endswith(".wav"):
            file_path = os.path.join(target_path, filename)
            audio = AudioSegment.from_wav(file_path)
            audio_length_ms = len(audio)
            # 再把过长的切片切一切
            if audio_length_ms > max_length_ms:
                num_slices = audio_length_ms // max_length_ms
                slice_length = audio_length_ms // num_slices

                for i in range(num_slices):
                    start_time = i * slice_length
                    end_time = (i + 1) * slice_length if i != num_slices - 1 else audio_length_ms
                    sliced_audio = audio[start_time:end_time]
                    slice_filename = f"{filename.rstrip('.wav')}_{i}.wav"
                    slice_path = os.path.join(target_path, slice_filename)

                    try:
                        sliced_audio.export(slice_path, format="wav")
                        print(f"Exported: {slice_path}")
                    except Exception as e:
                        print(f"Error exporting {slice_path}: {str(e)}")

                if not keep_file:
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        print(f"Error removing {file_path}: {str(e)}")

    file_count = len([f for f in os.listdir(target_path) if f.endswith(".wav")])
    print(f"切片完毕，产生了{file_count}个切片")
    return f"切片完毕，产生了{file_count}个切片"


def process_audio_files(file, max_wav_len, taboo_symbols, target_path, lang):
    result_str = ""
    rm_files = []

    target_path = update_status.raw_dir_convert_to_path(target_path, lang)
    print("target_path: ", target_path)
    try:
        os.makedirs(target_path, exist_ok=True)
    except:
        return "非法的目标文件路径，请重新输入", ""

    try:
        # 移动到目标文件夹
        extract_zip(file.name, target_path)

        convert_mp3_to_wav(target_path)
        # 处理每个wav文件
        for fname in os.listdir(target_path):
            if fname.lower().endswith(".wav"):
                with sf.SoundFile(os.path.join(target_path, fname)) as sound:
                    if len(sound) / sound.samplerate > max_wav_len:

                        rm_files.append(os.path.join(target_path, fname))
                        wav_file_to_remove = fname.replace(".WAV", ".wav").replace(".wav", ".lab")
                        if os.path.exists(
                                os.path.join(target_path, wav_file_to_remove)
                        ):
                            rm_files.append(
                                os.path.join(target_path, wav_file_to_remove)
                            )
                            result_str += f"[Too long] Deleted {fname} " \
                                          f"because it's longer than {max_wav_len} seconds.\n"
            elif fname.lower().endswith(".lab"):
                with open(
                        os.path.join(target_path, fname), "r", encoding="utf-8"
                ) as f:
                    content = f.read()
                    if any(char in content for char in taboo_symbols):
                        rm_files.append(os.path.join(target_path, fname))
                        wav_file_to_remove = fname.replace(".lab", ".wav")
                        if os.path.exists(
                                os.path.join(target_path, wav_file_to_remove)
                        ):
                            rm_files.append(os.path.join(target_path, fname))
                            result_str += f"[Invalid Chars] Deleted {wav_file_to_remove} and {fname} \n"

        if result_str:
            print(result_str)
            for it in rm_files:
                if os.path.exists(it):
                    os.remove(it)
        print("解析音频文件完成")
        return update_status.update_raw_folders()

    except Exception as e:
        return str(e)


def clear_temp_files():
    try:
        _tmp_folder = os.path.join(temp_folder, "gradio")
        if os.path.exists(_tmp_folder):
            shutil.rmtree(_tmp_folder)
            if os.path.exists('./separated'):
                shutil.rmtree(os.path.abspath('./separated'))
            if os.path.exists('./denoised_audio'):
                shutil.rmtree(os.path.abspath('./denoised_audio'))
            return "Removed temp_folder: " + _tmp_folder + ';./separated' + ';./denoised_audio'
        else:
            return "already cleaned"

    except Exception as e:
        return str(e)


# 函数：从视频中提取音频
def clip_file(file, video_dir, audio_dir):
    video_path = os.path.join(video_dir, file)
    os.makedirs(audio_dir, exist_ok=True)
    audio_path = os.path.join(audio_dir, file.replace(".mp4", ".wav"))
    my_audio_clip = AudioFileClip(video_path)
    my_audio_clip.write_audiofile(audio_path)
    print(video_path, ' ', audio_path)


def process_video_files(file, target_path, lang):
    target_path = update_status.raw_dir_convert_to_path(target_path, lang)
    print(file)
    file_name = file.name
    video_dir = os.path.join(os.path.dirname(file_name), 'video_dir', os.path.basename(target_path))
    os.makedirs(video_dir, exist_ok=True)
    video_dir_path = os.path.join(video_dir, os.path.basename(file_name))
    extract_zip(file_name, video_dir)
    print(f"zip文件已成功解压到 {video_dir}")
    # 获取视频文件列表并提取音频
    filelist = list(os.walk(video_dir))[0][2]
    print(filelist)
    videos = [_file for _file in filelist if _file.endswith(".mp4")]
    with ThreadPoolExecutor(max_workers=os.cpu_count()) as executor:
        executor.map(clip_file, videos, [video_dir] * len(videos), [target_path] * len(videos))
    print("解析视频文件完成")
    return update_status.update_raw_folders()


def fn_transcript(raw_folder="./raw"):
    os.makedirs("filelists", exist_ok=True)
    transcript_txt_file = "./filelists/genshin.list"
    print(raw_folder, "\n", transcript_txt_file)
    with open(transcript_txt_file, "w", encoding="utf-8") as f:
        # 遍历 raw 文件夹下的所有子文件夹
        for root, _, files in os.walk(raw_folder):
            for file in files:
                if file.endswith(".lab"):
                    lab_file_path = os.path.join(root, file)
                    # 提取文件夹名
                    folder_name = os.path.basename(root)
                    folder_name_suffix = folder_name[-3:]
                    # 读取转写文本
                    with open(lab_file_path, "r", encoding="utf-8") as lab_file:
                        transcription = lab_file.read().strip()
                    if len(transcription) == 0:
                        continue
                    # 获取对应的 WAV 文件路径
                    wav_file_path = os.path.splitext(lab_file_path)[0] + ".wav"
                    wav_file_path = wav_file_path.replace("\\", "/") \
                    .replace("./raw", "./dataset").replace("raw", "./dataset")
                    print(wav_file_path)
                    if folder_name_suffix in inv_lang_dict.keys():
                        # 写入数据到总的转写文本文件
                        line = f"{wav_file_path}|{folder_name}|{inv_lang_dict[folder_name_suffix]}|{transcription}\n"
                        f.write(line)
    return f"转写文本 {transcript_txt_file} 生成完成"



if __name__ == "__main__":
    with gr.Blocks(title="全流程处理", css="./css/gradio.css") as app:
        with gr.Row():
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2>一、上传原始数据</h2>")
            with gr.Column():
                gr.HTML("<h2>Powered By spicy-sama</h2>")
            with gr.Column():
                test_btn = gr.Button(value='测试按钮', variant="secondary")
            with gr.Column():
                pass
                # test_text = gr.Textbox(interactive=False, label="测试输出")
                # test_btn.click(
                #     extern_subprocess.do_test,
                #     inputs=[],
                #     outputs=[test_text]
                # )
        with gr.Row():
            with gr.Column():
                with gr.Row():
                    with gr.Column():
                        audio_folder = gr.inputs.File(
                            label="上传单个音频压缩包(.zip)",
                            type="file",
                            file_count="single"
                        )

                    with gr.Column():
                        video_folder = gr.inputs.File(
                            label="上传单个视频压缩包(.zip)",
                            type="file",
                            file_count="single"
                        )
                with gr.Row():
                    tar_path_dropdown = gr.Dropdown(
                        label="提取/转写音频目的路径",
                        info="必须是在项目的raw文件夹下的某个文件夹名称;\n没有该文件夹则可手动输入，解析后自动创建",
                        value="gongzi",
                        allow_custom_value=True,
                        interactive=True,
                    )
                    dropdown_lang = gr.Dropdown(
                        label="选择语言",
                        info="仅支持中文，日语\n目前还不能选择英语",
                        choices=list(lang_dict.keys()),
                        value="ZH(中文)",
                        interactive=True
                    )
                with gr.Row():
                    all_1_text = gr.Textbox(
                        label="1 输出信息",
                        placeholder="执行0/1.1/1.2/切片",
                        interactive=False,
                    )
                with gr.Row():
                    with gr.Column(min_width=100):
                        clear_btn = gr.Button(value="0. 清除临时文件", variant="secondary")
                    with gr.Column(min_width=100):
                        audio_submit_btn = gr.Button(value="1.1 解析音频压缩包", variant="primary")
                    with gr.Column(min_width=100):
                        video_submit_btn = gr.Button(value="1.2 解析视频压缩包", variant="primary")

                with gr.Row():
                    with gr.Column(min_width=100):
                        slider_min_wav_length = gr.Slider(
                            minimum=1, maximum=10, value=1, step=1, label="最小音频时长(秒)"
                        )
                    with gr.Column(min_width=100):
                        slider_max_wav_length = gr.Slider(
                            minimum=10, maximum=30, value=15, step=1, label="最大音频时长(秒)"
                        )

                    with gr.Column(min_width=100):
                        radio_detach = gr.Radio(
                            label="分离背景音和人声(demucs)", choices=["是", "否"], value="是", interactive=True
                        )
                with gr.Row():
                    with gr.Column(min_width=100):
                        slider_top_db = gr.Slider(
                            minimum=-80, maximum=0, value=-42, step=1, label="静音/振幅阈值(分贝全幅值dBFS)"
                        )
                    with gr.Column(min_width=100):
                        slider_min_slience = gr.Slider(
                            minimum=100, maximum=1000, value=300, step=1, label="最小静音间距(毫秒ms)"
                        )
                    with gr.Column(min_width=100):
                        slice_btn = gr.Button(value="继续切片", variant="secondary")
                with gr.Row():
                    slider_trans_workers = gr.Slider(minimum=1, maximum=12, value=1, step=1, label="转写进程数")
                    textbox_taboo_sym = gr.Textbox(
                        label="需要去掉的文本所包含的非法符号",
                        placeholder="输入所有需要屏蔽的符号",
                        value=taboo_symbols,
                        interactive=True,
                    )

                with gr.Row():
                    do_transcript_btn = gr.Button(value="1.3 转写音频一一对应到文本")
                    with gr.Column(min_width=150):
                        textbox_transcript_num = gr.Textbox(label="对应转写文本数/音频数",
                                                            value=update_status.update_wav_lab_pairs(),
                                                            interactive=False, placeholder="N/A")

                with gr.Row():
                    transcript_btn = gr.Button(value="1.4 提取总转写文本到filelists下", variant="primary")
                with gr.Row():
                    with gr.Column():
                        textbox_transcript = gr.Textbox(
                            label="可以提取的角色音频/状态",
                            value=update_status.update_raw_folders()[0],
                        )
                    with gr.Column():
                        textbox_output_text = gr.Textbox(label="1.4 输出信息", placeholder="", lines=3)
            with gr.Column():
                image_1 = gr.Image(value=os.path.abspath("./img/神里绫华.png")
                                   , show_label=False, show_download_button=False)

        # -----------------------------------------------------------
        with gr.Row():
            gr.HTML("<hr></hr>")
        with gr.Row():
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2>二、预处理数据</h2>")
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2><a href=\"https://space.bilibili.com/47278440\">访问我的bilibili主页</a></h2>")
            with gr.Column():
                pass

        with gr.Row():
            with gr.Column():
                with gr.Row():
                    resample_btn = gr.Button(value="2.1 音频重采样到44100hz(音频从raw转到dataset)", variant="primary")
                with gr.Row():
                    preprocess_text_btn = gr.Button(value="2.2 生成训练集和验证集标注文本", variant="primary")
                with gr.Row():
                    slider_bert_gen = gr.Slider(value=2, step=1, label="bert处理线程数", minimum=1, maximum=12)
                with gr.Row():
                    bert_gen_btn = gr.Button(value="2.3 生成bert文件(语调、停顿)", variant="primary")
                with gr.Row():
                    all_2_text = gr.Textbox(label="2 输出信息", placeholder="", lines=3)
            with gr.Column():
                image_2 = gr.Image(value=os.path.abspath("./img/宵宫.png")
                                   , show_label=False, show_download_button=False)

        # ----------------------------------------------------------------------
        with gr.Row():
            gr.HTML("<hr></hr>")
        with gr.Row():
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2>三、训练语音模型</h2>")
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2><a href=\"https://github.com/AnyaCoder\">访问我的github主页(求晶哥放过)</a></h2>")
            with gr.Column():
                pass

        with gr.Row():
            with gr.Column():
                with gr.Row():
                    with gr.Column():
                        model_dir_dropdown = gr.Dropdown(label="选择模型文件夹名称(即底模所在的位置，没有则会在./logs下创建文件夹)",
                                                         value="mix",
                                                         allow_custom_value=True,
                                                         interactive=True)
                with gr.Row():
                    slider_batch_size = gr.Slider(minimum=1, maximum=40, value=4, step=1,
                                                  label="batch_size 批处理大小")
                    slider_keep_ckpts = gr.Slider(minimum=1, maximum=20, value=5, step=1,
                                                  label="最多保存n个最新模型，超过则删除最早的")
                with gr.Row():
                    slider_log_interval = gr.Slider(minimum=50, maximum=3000, value=200, step=50,
                                                    label="log_interval 打印日志步数间隔")
                    slider_eval_interval = gr.Slider(minimum=500, maximum=5000, value=1000, step=50,
                                                     label="eval_interval 保存模型步数间隔")
                with gr.Row():
                    slider_epochs = gr.Slider(minimum=100, maximum=10000, value=1000, step=100,
                                              label="epochs 训练轮数")
                    slider_lr = gr.Slider(minimum=0.0001, maximum=0.0010, value=0.0003, step=0.0001,
                                          label="learning_rate 学习率")
                with gr.Row():
                    with gr.Column():
                        train_btn = gr.Button(value="3.1 点击开始训练", variant="primary")
                    with gr.Column():
                        train_btn_2 = gr.Button(value="3.2 继续训练", variant="primary")
                with gr.Row():
                    stop_train_btn = gr.Button(value="终止训练（已弃用，请手动关闭窗口）", variant="secondary")

                with gr.Row():
                    all_3_text = gr.Textbox(label="3 输出信息", placeholder="")
            with gr.Column():
                image_3 = gr.Image(value=os.path.abspath("./img/纳西妲.png")
                                   , show_label=False, show_download_button=False)
        # ----------------------------------------------------------------------
        # ----------------------------------------------------------------------
        with gr.Row():
            gr.HTML("<hr></hr>")
        with gr.Row():
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2>四、推理合成音频</h2>")
            with gr.Column():
                pass
            with gr.Column():
                gr.HTML("<h2><a href=\"https://www.codewithgpu.com/u/spicysama\">访问我的autodl主页</a></h2>")
            with gr.Column():
                pass

        with gr.Row():
            with gr.Column():
                with gr.Row():
                    with gr.Column():
                        infer_path_dropdown = gr.Dropdown(label="选择模型G_xxxx.pth(请放在logs的某个文件夹下)",
                                                          interactive=True)
                    with gr.Column():
                        config_path_dropdown = gr.Dropdown(label="选择配置文件config.json(请放在logs的某个文件夹下)",
                                                           interactive=True)
                with gr.Row():
                    infer_btn = gr.Button(value="4.1 点击开始推理", variant="primary")
                with gr.Row():
                    stop_infer_btn = gr.Button(value="终止推理（已弃用，请手动关闭窗口）", variant="secondary")
                with gr.Row():
                    all_4_text = gr.Textbox(label="4 输出信息", placeholder="")
            with gr.Column():
                image_4 = gr.Image(value=os.path.abspath("./img/yuyu.png")
                                   , show_label=False, show_download_button=False)
        # ----------------------------------------------------------------------
        model_dir_dropdown.change(fn=update_status.update_model_folders, inputs=[],
                                  outputs=[all_3_text, model_dir_dropdown])
        infer_path_dropdown.change(fn=update_status.update_g_files, inputs=[],
                                   outputs=[all_4_text, infer_path_dropdown])
        config_path_dropdown.change(fn=update_status.update_c_files, inputs=[],
                                    outputs=[all_4_text, config_path_dropdown])
        tar_path_dropdown.change(fn=update_status.update_raw_folders, inputs=[],
                                  outputs=[all_1_text, tar_path_dropdown, textbox_transcript_num])
        slice_btn.click(
            slice_audio,
            inputs=[tar_path_dropdown, dropdown_lang, slider_top_db, slider_min_slience,
                    slider_min_wav_length, slider_max_wav_length, radio_detach],
            outputs=[all_1_text]
        )
        do_transcript_btn.click(
            extern_subprocess.do_transcribe,
            inputs=[tar_path_dropdown, dropdown_lang, slider_trans_workers],
            outputs=[all_1_text]
        )
        clear_btn.click(
            clear_temp_files,
            outputs=[
                all_1_text,
            ],
        )
        audio_submit_btn.click(
            process_audio_files,
            inputs=[
                audio_folder,
                slider_max_wav_length,
                textbox_taboo_sym,
                tar_path_dropdown,
                dropdown_lang,
            ],
            outputs=[all_1_text, tar_path_dropdown, textbox_transcript_num],
        )
        video_submit_btn.click(
            process_video_files,
            inputs=[
                video_folder,
                tar_path_dropdown,
                dropdown_lang,
            ],
            outputs=[all_1_text, tar_path_dropdown, textbox_transcript_num],
        )
        transcript_btn.click(
            fn_transcript, inputs=[], outputs=[textbox_output_text]
        )
        preprocess_text_btn.click(
            extern_subprocess.do_preprocess_text,
            inputs=[],
            outputs=[all_2_text]
        )
        resample_btn.click(
            extern_subprocess.do_resample,
            inputs=[],
            outputs=[all_2_text]
        )
        bert_gen_btn.click(
            extern_subprocess.do_bert_gen,
            inputs=[slider_bert_gen],
            outputs=[all_2_text]
        )
        stop_train_btn.click(
            extern_subprocess.terminate_training,
            inputs=[],
            outputs=[all_3_text]
        )
        train_btn.click(
            extern_subprocess.do_training,
            inputs=[model_dir_dropdown, slider_batch_size, slider_log_interval, slider_eval_interval,
                    slider_epochs, slider_lr, slider_keep_ckpts],
            outputs=[all_3_text]
        )
        train_btn_2.click(
            extern_subprocess.do_training,
            inputs=[model_dir_dropdown, slider_batch_size, slider_log_interval, slider_eval_interval,
                    slider_epochs, slider_lr, slider_keep_ckpts],
            outputs=[all_3_text]
        )
        stop_infer_btn.click(
            extern_subprocess.terminate_webui,
            inputs=[],
            outputs=[all_4_text]
        )
        infer_btn.click(
            extern_subprocess.do_inference_webui,
            inputs=[infer_path_dropdown, config_path_dropdown],
            outputs=[all_4_text]
        )
        gr.HTML("<hr></hr>")  # 这里添加了分割线

    # -------------------------------------
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    # webbrowser.open("http://127.0.0.1:6006")
    app.launch(share=args.share, server_port=6006)
