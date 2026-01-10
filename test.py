import numpy as np
import matplotlib.pyplot as plt

# y = x^-4 = 1/x^4，注意 x=0 不可取，所以分两段画
x1 = np.linspace(-3, -0.1, 2000)
x2 = np.linspace(0.1, 3, 2000)

y1 = 1 / (x1**4)
y2 = 1 / (x2**4)

plt.figure()
plt.plot(x1, y1, label=r"$y=x^{-4}=\frac{1}{x^4}$")
plt.plot(x2, y2)

# 画坐标轴 & 渐近线提示
plt.axhline(0)
plt.axvline(0, linestyle="--")

plt.ylim(0, 10)          # 你想看更“尖”就把 10 改大，比如 200
plt.xlim(-3, 3)
plt.grid(True)
plt.legend()
plt.show()
