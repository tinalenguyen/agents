# Copyright 2023 LiveKit, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)

from google.api_core.exceptions import DeadlineExceeded, GoogleAPICallError
from google.cloud import texttospeech
from google.cloud.texttospeech_v1.types import SsmlVoiceGender, SynthesizeSpeechResponse

from .models import Gender, SpeechLanguages


@dataclass
class _TTSOptions:
    voice: texttospeech.VoiceSelectionParams
    audio_config: texttospeech.AudioConfig


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        language: SpeechLanguages | str = "en-US",
        gender: Gender | str = "neutral",
        voice_name: str = "",  # Not required
        sample_rate: int = 24000,
        pitch: int = 0,
        effects_profile_id: str = "",
        speaking_rate: float = 1.0,
        credentials_info: dict | None = None,
        credentials_file: str | None = None,
    ) -> None:
        """
        Create a new instance of Google TTS.

        Credentials must be provided, either by using the ``credentials_info`` dict, or reading
        from the file specified in ``credentials_file`` or the ``GOOGLE_APPLICATION_CREDENTIALS``
        environmental variable.

        Args:
            language (SpeechLanguages | str, optional): Language code (e.g., "en-US"). Default is "en-US".
            gender (Gender | str, optional): Voice gender ("male", "female", "neutral"). Default is "neutral".
            voice_name (str, optional): Specific voice name. Default is an empty string.
            sample_rate (int, optional): Audio sample rate in Hz. Default is 24000.
            pitch (float, optional): Speaking pitch, ranging from -20.0 to 20.0 semitones relative to the original pitch. Default is 0.
            effects_profile_id (str): Optional identifier for selecting audio effects profiles to apply to the synthesized speech.
            speaking_rate (float, optional): Speed of speech. Default is 1.0.
            credentials_info (dict, optional): Dictionary containing Google Cloud credentials. Default is None.
            credentials_file (str, optional): Path to the Google Cloud credentials JSON file. Default is None.
        """

        super().__init__(
            capabilities=tts.TTSCapabilities(
                streaming=False,
            ),
            sample_rate=sample_rate,
            num_channels=1,
        )

        self._client: texttospeech.TextToSpeechAsyncClient | None = None
        self._credentials_info = credentials_info
        self._credentials_file = credentials_file

        voice = texttospeech.VoiceSelectionParams(
            name=voice_name,
            language_code=language,
            ssml_gender=_gender_from_str(gender),
        )

        self._opts = _TTSOptions(
            voice=voice,
            audio_config=texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.OGG_OPUS,
                sample_rate_hertz=sample_rate,
                pitch=pitch,
                effects_profile_id=effects_profile_id,
                speaking_rate=speaking_rate,
            ),
        )

    def update_options(
        self,
        *,
        language: SpeechLanguages | str = "en-US",
        gender: Gender | str = "neutral",
        voice_name: str = "",  # Not required
        speaking_rate: float = 1.0,
    ) -> None:
        """
        Update the TTS options.

        Args:
            language (SpeechLanguages | str, optional): Language code (e.g., "en-US"). Default is "en-US".
            gender (Gender | str, optional): Voice gender ("male", "female", "neutral"). Default is "neutral".
            voice_name (str, optional): Specific voice name. Default is an empty string.
            speaking_rate (float, optional): Speed of speech. Default is 1.0.
        """
        self._opts.voice = texttospeech.VoiceSelectionParams(
            name=voice_name,
            language_code=language,
            ssml_gender=_gender_from_str(gender),
        )
        self._opts.audio_config.speaking_rate = speaking_rate

    def _ensure_client(self) -> texttospeech.TextToSpeechAsyncClient:
        if self._client is None:
            if self._credentials_info:
                self._client = (
                    texttospeech.TextToSpeechAsyncClient.from_service_account_info(
                        self._credentials_info
                    )
                )

            elif self._credentials_file:
                self._client = (
                    texttospeech.TextToSpeechAsyncClient.from_service_account_file(
                        self._credentials_file
                    )
                )
            else:
                self._client = texttospeech.TextToSpeechAsyncClient()

        assert self._client is not None
        return self._client

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> "ChunkedStream":
        return ChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            opts=self._opts,
            client=self._ensure_client(),
        )


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        opts: _TTSOptions,
        client: texttospeech.TextToSpeechAsyncClient,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._opts, self._client = opts, client

    async def _run(self) -> None:
        request_id = utils.shortuuid()

        try:
            response: SynthesizeSpeechResponse = await self._client.synthesize_speech(
                input=texttospeech.SynthesisInput(text=self._input_text),
                voice=self._opts.voice,
                audio_config=self._opts.audio_config,
                timeout=self._conn_options.timeout,
            )

            # Create AudioStreamDecoder for OGG format
            decoder = utils.codecs.AudioStreamDecoder(
                sample_rate=self._opts.audio_config.sample_rate_hertz,
                num_channels=1,
            )

            try:
                decoder.push(response.audio_content)
                decoder.end_input()
                emitter = tts.SynthesizedAudioEmitter(
                    event_ch=self._event_ch,
                    request_id=request_id,
                )
                async for frame in decoder:
                    emitter.push(frame)
                emitter.flush()
            finally:
                await decoder.aclose()

        except DeadlineExceeded:
            raise APITimeoutError()
        except GoogleAPICallError as e:
            raise APIStatusError(
                e.message,
                status_code=e.code or -1,
                request_id=None,
                body=None,
            )
        except Exception as e:
            raise APIConnectionError() from e


def _gender_from_str(gender: str) -> SsmlVoiceGender:
    ssml_gender = SsmlVoiceGender.NEUTRAL
    if gender == "male":
        ssml_gender = SsmlVoiceGender.MALE
    elif gender == "female":
        ssml_gender = SsmlVoiceGender.FEMALE

    return ssml_gender  # type: ignore
