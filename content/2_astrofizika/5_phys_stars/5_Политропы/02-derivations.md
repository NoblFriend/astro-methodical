## 1. Уравнение Лейна-Эмдена

Выведем уравнение Лейна-Эмдена, которое является одним из важнейших в теории политроп и вообще в физике звёзд. Мы будем исходить из следующих выражений:

$$
\frac{dP}{dr} = -\frac{G M(r) \rho}{r^2}, \qquad
\frac{dM(r)}{dr} = 4\pi r^2 \rho, \qquad
P = K \rho^{1 + 1/n}
$$

Перенесём всё, кроме $M(r)$ в первом уравнении вправо и продифференциируем по $r$ обе части. Отсюда получим:

$$
\frac{d}{dr}
\left(
-\frac{dP}{dr}\frac{r^2}{G\rho}
\right)
=
\frac{dM(r)}{dr}
$$

Раскрывая производную и соединяя со вторым уравнением имеем:

$$
-\frac{r^2}{G\rho}\frac{d^2P}{dr^2}
-\frac{2r}{G\rho}\frac{dP}{dr}
+\frac{r^2}{G\rho^2}\frac{dP}{dr}\frac{d\rho}{dr}
=
4\pi r^2\rho
$$

Теперь сократим обе части на $r$, домножим на $G\rho$:

$$
r\frac{d^2P}{dr^2}
+2\frac{dP}{dr}
-\frac{r}{\rho}\frac{dP}{dr}\frac{d\rho}{dr}
+4\pi G r\rho^2
=
0
$$

Теперь, используя уравнение политропы решим это уравнение, то есть найдём распределение давления/плотности по радиусу. Выразим производные давления через производную плотности:

$$
\frac{dP}{dr}
=
\frac{d(K\rho^{1+1/n})}{dr}
=
\left(1+\frac1n\right)
K\rho^{1/n}\frac{d\rho}{dr}
$$

$$
\frac{d^2P}{dr^2}
=
\frac{1+1/n}{n}
K\rho^{1/n-1}
\left(\frac{d\rho}{dr}\right)
+
\left(1+\frac1n\right)
K\rho^{1/n}
\frac{d^2\rho}{dr^2}
$$

Предварительно разделив всё на $(1+1/n)Kr\rho^{1/n-1}$ и подставляя, получим:

$$
\boxed{
\rho\frac{d^2\rho}{dr^2}
+
\frac{d\rho}{dr}
\left(
\frac{2\rho}{r}
+\frac1n
\right)
-
\left(\frac{d\rho}{dr}\right)^2
+
\frac{n}{n+1}
\frac{4\pi G}{K}
\rho^{3-1/n}
=
0
}
$$

Это уравнение и называется уравнением Лейна-Эмдена, а его решением является распределение плотности по радиусу для политропной модели звезды. Теперь получим его в безразмерном виде.

Для этого вернёмся немного назад и получим выражение для гравитационного потенциала $\phi$.

$$
\frac{dP}{dr}
=
-\frac{GM(r)\rho}{r^2}
=
-\frac{d\phi}{dr}\rho
$$

Подставляя уравнение политропы получим:

$$
\left(1+\frac1n\right)
K\rho^{1/n}
\frac{d\rho}{dr}
=
-\frac{d\phi}{dr}\rho
$$

Примем потенциал равным $0$ на поверхности звезды. Тогда, интегрируя (считая также, что плотность на поверхности также равна $0$), получим:

$$
\boxed{
\rho
=
\left(
\frac{\phi}{(1+n)K}
\right)^n
\Rightarrow
\phi=(1+n)K\rho^{1/n}
}
$$

Теперь введём безразмерный потенциал $\theta$, выраженный в долях от центрального $\phi_0$:

$$
\theta=\frac{\phi}{\phi_0}
\Rightarrow
\rho
=
\left(
\frac{\phi_0}{(1+n)K}
\right)^n
\theta^n
=
\rho_0\theta^n
$$

Здесь $\rho_0$ -- плотность в центре звезды.

Теперь вернёмся к первому шагу, когда мы дифференциировали обе части первого уравнения и подставим туда полученные выше соотношения (а также сразу выразим производную давления):

$$
\frac{dP}{dr}
=
\left(1+\frac1n\right)
K\rho^{1/n}
\frac{d\rho}{dr}
=
(1+n)K\rho_0^{1+1/n}
\theta^n
\frac{d\theta}{dr}
$$

$$
\frac{d}{dr}
\left(
-\frac{dP}{dr}\frac{r^2}{G\rho}
\right)
=
\frac{d}{dr}
\left(
-\frac{(1+n)K\rho_0^{1+1/n}r^2}{G\rho_0}
\frac{d\theta}{dr}
\right)
=
4\pi r^2\rho_0\theta^n
$$

$$
\frac{d}{dr}
\left(
\frac{(1+n)Kr^2\rho_0^{1/n}}{G}
\frac{d\theta}{dr}
\right)
+
4\pi r^2\rho_0\theta^n
=
0
$$

Продолжая преобразовывать, получим:

$$
\frac1{r^2}
\frac{d}{dr}
\left(
r^2
\frac{(1+n)K\rho_0^{1/n-1}}{4\pi G}
\frac{d\theta}{dr}
\right)
+
\theta^n
=
0
$$

Теперь введём безразмерное расстояние $\xi$, но отнормируем его не на радиус, а на величину $r_1$ с размерностью расстояния так, чтобы уравнение максимально упростилось. Пусть:

$$
\xi=\frac{r}{r_1},
\qquad
r_1=
\sqrt{
\frac{(1+n)K\rho_0^{1/n-1}}{4\pi G}
}
=
\sqrt{
\frac{\phi_0}{4\pi G\rho_0}
}
$$

Подставляя, получим:

$$
\frac{r_1^2}{r^2}
\frac{d}{dr}
\left(
r^2\frac{d\theta}{dr}
\right)
+
\theta^n
=
0,
\qquad
dr=r_1d\xi
$$

$$
\frac1{\xi^2}
\frac{d}{d\xi}
\left(
\xi^2\frac{d\theta}{d\xi}
\right)
+
\theta^n
=
0
$$

Итого, мы получаем уравнение Лейна-Эмдена в удобной для нас форме:

$$
\boxed{
\frac1{\xi^2}
\frac{d}{d\xi}
\left(
\xi^2\frac{d\theta}{d\xi}
\right)
+
\theta^n
=
0
}
$$