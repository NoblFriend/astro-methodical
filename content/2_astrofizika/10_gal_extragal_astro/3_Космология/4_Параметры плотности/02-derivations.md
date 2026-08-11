## 1. Уравнение Фридмана через параметры плотности

Связем параметры плотности с постоянной Хаббла через критическую плотность. С одной стороны из $\eqref{sum_om}$:

$$
\rho_{cr} = \rho_m + \rho_r + \rho_\Lambda + \Omega_k \cdot \rho_{cr}
$$

С другой из определения критической плотности:

$$
H^2 \sim \rho_{cr}
$$

Зафиксируем "нулевой" момент времени, в который $a_0 = 1$. Тогда:

$$
\frac{H^2}{H_0^2} = \frac{\rho_{cr}}{\rho_{cr 0}} = \frac{\rho_m + \rho_r + \rho_\Lambda + \Omega_k \cdot \rho_{cr}}{\rho_{cr 0}}
$$

Выразим это через $\Omega_0$ - параметры плотности в "нулевой" момент времени:

$$
\frac{H^2}{H_0^2} = \frac{\rho_{m 0}}{\rho_{cr 0}} \cdot \frac{\rho_{m}}{\rho_{m 0}} + \frac{\rho_{r 0}}{\rho_{cr 0}} \cdot \frac{\rho_{r}}{\rho_{r 0}} + \frac{\rho_{\Lambda 0}}{\rho_{cr 0}} \cdot \frac{\rho_{\Lambda}}{\rho_{\Lambda 0}} + \Omega_k \cdot \frac{\rho_{cr}}{\rho_{cr 0}}
$$

$$
\frac{H^2}{H_0^2} = \Omega_{m 0} \cdot \frac{\rho_{m}}{\rho_{m 0}} + \Omega_{r 0} \cdot \frac{\rho_{r}}{\rho_{r 0}} + \Omega_{\Lambda 0} \cdot \frac{\rho_{\Lambda}}{\rho_{\Lambda 0}} + \Omega_k \cdot \frac{\rho_{cr}}{\rho_{cr 0}}
$$

Распишем слагаемое с параметром плотности кривизны:

$$
\Omega_k \cdot \frac{\rho_{cr}}{\rho_{cr 0}} = -\frac{K c^2}{a^2 H^2} \cdot \frac{\rho_{cr}}{\rho_{cr 0}} = -\frac{K c^2}{a^2 H^2} \cdot \frac{H^2}{H_0^2}
$$

$$
\Omega_k \cdot \frac{\rho_{cr}}{\rho_{cr 0}} = -\frac{K c^2}{a_0^2 H_0^2} \frac{a_0^2}{a^2} = \Omega_{k 0} \cdot a^{-2}
$$

Зависимости плотности от масштабного фактора были получены нами ранее, например здесь $\eqref{rho_mat}$. Подставляя, получаем уравнение Фридмана:

$$
\boxed{
H^2 = H_0^2 \cdot (\Omega_{m 0} a^{-3} + \Omega_{r 0} a^{-4} + \Omega_{\Lambda 0} + \Omega_{k 0} a^{-2})
}
$$

