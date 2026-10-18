# Homebrew formula skeleton for Dotiphant.
#
# Status: draft. It cannot be installed until the first release is on PyPI.
# The formula, the command and the PyPI distribution are all named dotiphant.
# Lives in a personal tap first (github.com/zierold-ventures/homebrew-tap,
# file Formula/dotiphant.rb), so users run:
#
#   brew install zierold-ventures/tap/dotiphant
#
# See docs/releasing.md ("Homebrew") for how to fill in the TODOs.
class Dotiphant < Formula
  include Language::Python::Virtualenv

  desc "Telegram chats as a local, searchable archive that AI assistants can read"
  homepage "https://github.com/Zierold-Ventures/dotiphant"
  url "https://files.pythonhosted.org/packages/source/p/dotiphant/dotiphant-0.1.0.tar.gz"
  # TODO: sha256 of the sdist above, from the PyPI "Download files" page or
  # `curl -sL URL | shasum -a 256`.
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"
  license "MIT"

  depends_on "rust" => :build # pydantic-core and rpds-py build from source
  depends_on "certifi"
  depends_on "cryptography"
  depends_on "python@3.13"

  # TODO: generate every runtime dependency as a resource block after the first
  # release, then paste them here:
  #
  #   brew update-python-resources --print-only Formula/dotiphant.rb
  #
  # Leave out dev-only packages (pytest, pytest-asyncio, httpx used only by tests),
  # Windows-only packages (pywin32, pywin32-ctypes, colorama) and the packages
  # provided by the depends_on lines above (certifi, cryptography, cffi, pycparser).
  #
  # resource "jinja2" do
  #   url "https://files.pythonhosted.org/packages/.../jinja2-3.1.6.tar.gz"
  #   sha256 "..."
  # end

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      Start with:
        dotiphant open
      Dotiphant stores your archive in ~/Library/Application Support/Dotiphant (macOS)
      or ~/.local/share/dotiphant (Linux). See `dotiphant status`.
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/dotiphant --version")
    ENV["DOTIPHANT_HOME"] = testpath/"home"
    ENV["DOTIPHANT_SECRETS"] = "file"
    # doctor exits 1 on a fresh install because there is no archive yet.
    assert_match "Data folder is writable", shell_output("#{bin}/dotiphant doctor", 1)
  end
end
